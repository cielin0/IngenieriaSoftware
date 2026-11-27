import hashlib

from django.contrib.auth.hashers import BasePasswordHasher, mask_hash
from django.utils.crypto import constant_time_compare


class LegacyScryptPasswordHasher(BasePasswordHasher):
    algorithm = "legacy_scrypt"

    def salt(self):
        return super().salt()

    def encode(self, password, salt):
        digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1, dklen=64)
        return f"{self.algorithm}${salt}${digest.hex()}"

    def verify(self, password, encoded):
        algorithm, salt, expected = encoded.split("$", 2)
        if algorithm != self.algorithm:
            return False
        actual = self.encode(password, salt).rsplit("$", 1)[-1]
        return constant_time_compare(actual, expected)

    def decode(self, encoded):
        algorithm, salt, checksum = encoded.split("$", 2)
        return {"algorithm": algorithm, "salt": salt, "checksum": checksum}

    def safe_summary(self, encoded):
        decoded = self.decode(encoded)
        return {
            "algorithm": decoded["algorithm"],
            "salt": mask_hash(decoded["salt"]),
            "hash": mask_hash(decoded["checksum"]),
        }

    def must_update(self, encoded):
        return False
