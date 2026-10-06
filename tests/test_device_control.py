"""Check platform commands produce the expected media lifecycle and replies."""

import threading
from types import SimpleNamespace
import unittest

from isup_gateway.device_session import DeviceSession
from isup_gateway.isup_protocol import parse_publish, parse_business
from isup_gateway.isup_media import media_frames, parse_rtp, send_media_header


class Sender:
    def __init__(self):
        self.session_id = None
        self.starts = []
        self.stops = 0
        self.iframe_event = threading.Event()

    def start(self, host, port, session_id):
        self.starts.append((host, port, session_id))
        self.session_id = session_id

    def stop(self):
        self.stops += 1
        self.session_id = None


class Wire:
    def __init__(self):
        self.data = bytearray()

    def sendall(self, data):
        self.data.extend(data)


class DeviceControlTests(unittest.TestCase):
    def setUp(self):
        self.sender = Sender()
        self.device = DeviceSession(
            SimpleNamespace(device_id="TEST001", name="Test camera"), self.sender
        )
        self.key = b"0123456789ABCDEF"
        self.meta = {"Seq": 123, "CmdVer": "1.7"}

    def command(self, topic, body):
        wire = Wire()
        self.device.command(wire, topic, self.meta, body, self.key)
        data = wire.data
        offset = 1
        size = 0
        for shift in range(0, 28, 7):
            digit = data[offset]
            offset += 1
            size |= (digit & 127) << shift
            if not digit & 128:
                break
        self.assertEqual(size, len(data) - offset)
        reply_topic, packet_id, plain = parse_publish(data[0], bytes(data[offset:]), self.key)
        metadata, reply = parse_business(plain)
        self.assertEqual(metadata["Seq"], 123)
        self.assertGreater(packet_id, 0)
        return reply_topic, reply

    def test_start_iframe_stop_preserve_session_identity(self):
        body = (
            b'<Request><ReceiverInfo TransProto="TCP" Address="192.0.2.10" Port="7665"/>'
            b'<ReceiverInfoEx SessionID="2049" Port="17665"/>'
            b"<IsEncrypt>0</IsEncrypt><IsLinkEncrypt>0</IsLinkEncrypt></Request>"
        )
        topic, reply = self.command("/TEST001/3101/12545", body)
        self.assertEqual(topic, "/3101/12546")
        self.assertIn(b"<Result>0</Result>", reply)
        self.assertEqual(self.sender.starts, [("192.0.2.10", 17665, 2049)])
        self.command("/TEST001/3100/13416", b"<Request/>")
        self.assertTrue(self.sender.iframe_event.is_set())
        # An old session stop must not close the current media connection.
        self.command("/TEST001/3101/12547", b"<Request><Session>1</Session></Request>")
        self.assertEqual(self.sender.stops, 0)
        self.command("/TEST001/3101/12547", b"<Request><Session>2049</Session></Request>")
        self.assertEqual(self.sender.stops, 1)

    def test_encrypted_media_request_is_rejected(self):
        body = (
            b'<Request><ReceiverInfo TransProto="TCP" Address="192.0.2.10" Port="7665"/>'
            b'<ReceiverInfoEx SessionID="1" Port="7665"/><IsEncrypt>1</IsEncrypt></Request>'
        )
        _, reply = self.command("/TEST001/3101/12545", body)
        self.assertNotIn(b"<Result>0</Result>", reply)
        self.assertEqual(self.sender.starts, [])

    def test_media_header_binds_receiver_session_and_hevc_descriptors(self):
        wire = Wire()
        send_media_header(wire, 2049, 0x11223344)
        frames = list(media_frames(bytes(wire.data)))
        self.assertEqual(frames[0], (1, b"2049" + bytes(60)))
        self.assertEqual(frames[1][1][4:8], b"IMKH")
        self.assertEqual(len(frames[1][1]), 44)
        self.assertEqual(len(frames), 4)
        for kind, payload in frames[2:]:
            self.assertEqual(kind, 2)
            self.assertEqual(payload[:4], bytes(4))
            rtp = parse_rtp(payload[4:])
            self.assertEqual(rtp.payload_type, 112)
            self.assertEqual(rtp.ssrc, 0x11223344)


if __name__ == "__main__":
    unittest.main()
