import socket
import struct
import unittest

from isup_gateway.aes_backend import aes_cbc
from isup_gateway.isup_protocol import (
    mqtt_length,
    packet,
    read_packet,
    encrypt,
    decrypt,
    business_message,
    parse_business,
)
from isup_gateway.isup_media import parse_rtp, HEVCDepacketizer, hevc_rtp, media_frames, media_frame


class ProtocolTests(unittest.TestCase):
    def test_nist_aes_cbc_vector(self):
        # NIST SP 800-38A F.2.1, first block. Independent known-answer test.
        key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plain = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        expected = bytes.fromhex("7649abac8119b246cee98e9b12e9197d")
        self.assertEqual(aes_cbc(key, iv, plain, True), expected)
        self.assertEqual(aes_cbc(key, iv, expected, False), plain)

    def test_packet_lengths_and_fragmented_transport(self):
        expected = {
            0: b"\0",
            127: b"\x7f",
            128: b"\x80\1",
            16384: b"\x80\x80\1",
            268435455: b"\xff\xff\xff\x7f",
        }
        for value, encoded in expected.items():
            self.assertEqual(mqtt_length(value), encoded)
        left, right = socket.socketpair()
        try:
            body = bytes(range(128))
            for chunk in [packet(0x32, body)[:1], packet(0x32, body)[1:3], body]:
                left.sendall(chunk)
            self.assertEqual(read_packet(right), (0x32, body))
            left.sendall(b"\x30\x80\x80\x80\x80")
            with self.assertRaises(ValueError):
                read_packet(right)
        finally:
            left.close()
            right.close()

    def test_padding_and_business_boundaries(self):
        key = b"0123456789ABCDEF"
        for value in [b"", b"a", bytes(16), bytes(17), bytes(255)]:
            self.assertEqual(decrypt(key, encrypt(key, value)), value)
        invalid = aes_cbc(key, b"01234567" + bytes(8), bytes(16), True)
        with self.assertRaises(ValueError):
            decrypt(key, invalid)
        self.assertEqual(
            parse_business(business_message({"Seq": 123}, b"<Request/>")),
            ({"Seq": 123}, b"<Request/>"),
        )
        with self.assertRaises(ValueError):
            parse_business(b"\xff\xff{}")

    def test_hevc_fragments_wrap_and_packet_loss(self):
        nal = b"\x26\x01" + bytes(range(256)) * 12
        packets = [parse_rtp(raw) for seq, raw in hevc_rtp(nal, 65534, 90000, 0x55667788, True)]
        self.assertEqual([p.sequence for p in packets], [65534, 65535, 0])
        depacketizer = HEVCDepacketizer()
        out = []
        for rtp in packets:
            out.extend(depacketizer.feed(rtp))
        self.assertEqual(out, [nal])
        lost = HEVCDepacketizer()
        lost.feed(packets[0])
        self.assertEqual(lost.feed(packets[2]), [])
        self.assertEqual(lost.dropped, 1)

    def test_rtp_extension_padding_and_media_truncation(self):
        raw = (
            struct.pack("!BBHII", 0xB0, 0xE0, 10, 99, 123) + b"\x40\0\0\1ABCD" + b"\x40\x01\0\0\0\3"
        )
        parsed = parse_rtp(raw)
        self.assertEqual(parsed.payload, b"\x40\x01\0")
        self.assertTrue(parsed.marker)
        frame = media_frame(2, bytes(4) + raw)
        self.assertEqual(list(media_frames(frame)), [(2, bytes(4) + raw)])
        with self.assertRaises(ValueError):
            list(media_frames(frame[:-1]))
        with self.assertRaises(ValueError):
            parse_rtp(raw[:-1] + b"\0")


if __name__ == "__main__":
    unittest.main()
