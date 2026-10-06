"""AES-CBC via cryptography, or a configured public OpenSSL AES API.

The latter lets older Linux hosts run without installing modern binary wheels.
ABI verified against the server's /usr/include/openssl/aes.h and exported APIs.
This code does not invoke any Hikvision private authentication functions.
"""

import ctypes as C
import os


_openssl = None
if os.environ.get("ISUP_OPENSSL_LIB"):

    class AESKey(C.Structure):
        _fields_ = [("round_keys", C.c_uint * 60), ("rounds", C.c_int)]

    _openssl = C.CDLL(os.environ["ISUP_OPENSSL_LIB"])
    for name in ("AES_set_encrypt_key", "AES_set_decrypt_key"):
        func = getattr(_openssl, name)
        func.argtypes = [C.c_void_p, C.c_int, C.POINTER(AESKey)]
        func.restype = C.c_int
    _openssl.AES_cbc_encrypt.argtypes = [
        C.c_void_p,
        C.c_void_p,
        C.c_size_t,
        C.POINTER(AESKey),
        C.c_void_p,
        C.c_int,
    ]
    _openssl.AES_cbc_encrypt.restype = None
else:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def aes_cbc(key: bytes, iv: bytes, data: bytes, encrypting: bool) -> bytes:
    if len(key) != 16 or len(iv) != 16 or not data or len(data) % 16:
        raise ValueError("ISUP requires AES-128-CBC with complete blocks")
    if _openssl is not None:
        schedule = AESKey()
        keybuf = C.create_string_buffer(key)
        fn = _openssl.AES_set_encrypt_key if encrypting else _openssl.AES_set_decrypt_key
        if fn(keybuf, 128, C.byref(schedule)) or schedule.rounds != 10:
            raise ValueError("OpenSSL AES key setup failed or ABI does not match")
        inputbuf = C.create_string_buffer(data)
        outputbuf = C.create_string_buffer(len(data))
        ivbuf = C.create_string_buffer(iv)
        _openssl.AES_cbc_encrypt(
            inputbuf, outputbuf, len(data), C.byref(schedule), ivbuf, int(encrypting)
        )
        return outputbuf.raw
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    operation = cipher.encryptor() if encrypting else cipher.decryptor()
    return operation.update(data) + operation.finalize()
