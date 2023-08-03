import abc
import enum
import os
import random
import secrets
from datetime import datetime, timedelta
from multiprocessing import Queue, Process
from typing import ClassVar, Optional, Any, Type
from unittest import TestCase

from cryptography import x509
from cryptography.hazmat._oid import NameOID
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives._serialization import Encoding
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey, EllipticCurvePrivateKey
from fido2.client import UserInteraction, Fido2Client, _Ctap2ClientBackend
from fido2.cose import ES256
from fido2.ctap1 import Ctap1
from fido2.ctap2 import Ctap2, ClientPin, AttestationResponse, AssertionResponse, CredentialManagement, PinProtocolV2
from fido2.ctap2.base import args
from fido2.ctap2.extensions import Ctap2Extension
from fido2.pcsc import CtapPcscDevice
from fido2.webauthn import ResidentKeyRequirement, PublicKeyCredentialCreationOptions, PublicKeyCredentialUserEntity, \
    PublicKeyCredentialRpEntity, PublicKeyCredentialParameters, PublicKeyCredentialType, \
    AuthenticatorSelectionCriteria, UserVerificationRequirement, PublicKeyCredentialDescriptor, \
    AuthenticatorAttestationResponse, PublicKeyCredentialRequestOptions

import fido2.features

fido2.features.webauthn_json_mapping.enabled = False


class CommandType(enum.Enum):
    APPLET_REINSTALL = 0
    DIRECT_COMMUNICATE = 1
    SOFT_RESET = 2


class LogPrintHandler:
    level = 0

    def handle(self, r):
        print(r.msg % r.args)


class JCardSimTestCase(TestCase, abc.ABC):
    PCSC_MODE = False

    q_in: ClassVar[Queue]
    q_out: ClassVar[Queue]
    p: ClassVar[Process]

    DEBUG_PORT = 5005
    SUSPEND_ON_LAUNCH = False
    LOG_ABSOLUTELY_EVERYTHING = False

    @classmethod
    def start_jvm(cls):
        import jpype.imports

        my_path = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path_to_jars = os.path.join(my_path, 'build', 'libs')
        jars = os.listdir(path_to_jars)
        main_jars = []
        test_jars = []
        for jar in jars:
            if jar.startswith('fido2applet-tests-'):
                test_jars.append(jar)
            elif jar.startswith('fido2applet-'):
                main_jars.append(jar)
        if len(main_jars) == 0:
            raise ValueError("Applet not built - run ./gradlew jar")
        elif len(main_jars) > 1:
            raise ValueError("More than one main jar in build/libs - remove all but one")
        if len(test_jars) == 0:
            raise ValueError("Tests not built - run ./gradlew testJar")
        elif len(test_jars) > 1:
            raise ValueError("More than one test jar in build/libs - remove all but one")

        jc_home = os.environ.get("JC_HOME")
        if not jc_home:
            raise ValueError("$JC_HOME must be set to the path of your JavacardKit")
        jc_jars = os.path.join(jc_home, 'lib')

        classpath = [
            os.path.abspath(os.path.join(path_to_jars, main_jars[0])),  # Applet jar
            os.path.abspath(os.path.join(path_to_jars, test_jars[0])),  # Test support jar
        ]
        classpath += [
            os.path.join(jc_jars, x) for x in os.listdir(jc_jars)
        ]

        suspend_char = 'y' if cls.SUSPEND_ON_LAUNCH else 'n'

        jpype.startJVM(
            "-agentlib:jdwp=transport=dt_socket,server=y,"
            f"suspend={suspend_char},address={cls.DEBUG_PORT}",
            classpath=classpath
        )

    @classmethod
    def process(cls, VSim, sim, command_type, command) -> Optional[list[int]]:
        if command_type == CommandType.APPLET_REINSTALL:
            # Reset the simulator to fresh
            sim.resetRuntime()
            sim.reset()
            VSim.installApplet(sim, command)
            return None
        elif command_type == CommandType.SOFT_RESET:
            VSim.softReset(sim)
            return None
        elif command_type == CommandType.DIRECT_COMMUNICATE:
            result = VSim.transmitCommand(sim, bytes(command))
            return [(x + 256) % 256 for x in result]
        else:
            # Unknown command?
            return None

    @classmethod
    def launch_sim(cls, incoming_q: Queue, outgoing_q: Queue, startup_q: Queue):
        cls.start_jvm()
        from us.q3q.fido2 import VSim

        if cls.PCSC_MODE:
            sim = VSim.startBackgroundSimulator()
        else:
            sim = VSim.startForegroundSimulator()
        VSim.installApplet(sim, bytes())
        startup_q.put(None)
        while True:
            command_type, command = incoming_q.get(block=True)
            if command_type == CommandType.APPLET_REINSTALL and command is None:
                # We're done - exit
                break
            outgoing_q.put(cls.process(VSim, sim, command_type, command))

    @classmethod
    def setUpClass(cls) -> None:
        if cls.LOG_ABSOLUTELY_EVERYTHING:
            fido2.pcsc.logger.setLevel(0)
            fido2.pcsc.logger.disabled = False
            fido2.pcsc.logger.isEnabledFor = lambda x: True
            fido2.pcsc.logger.manager.disable = 0
            fido2.pcsc.logger.addHandler(LogPrintHandler())
            fido2.pcsc.logger._cache = {}
        cls.q_in = Queue(maxsize=1)
        cls.q_out = Queue(maxsize=1)
        q_startup = Queue(maxsize=1)
        cls.p = Process(target=cls.launch_sim, args=(cls.q_out, cls.q_in, q_startup))
        cls.p.start()
        q_startup.get(block=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.p.kill()
        cls.p.join()

    def setUp(self, install_params: Optional[bytes] = None) -> None:
        assert self.p.is_alive()
        if install_params is None:
            install_params = bytes()
        # Javacard install parameters are prefixed by AID and platform info
        ip_len = len(install_params)
        install_params = bytes([1, 95, 1, 86, ip_len]) + install_params
        self.q_out.put((CommandType.APPLET_REINSTALL, install_params))  # Tell JVM to reset applet state
        self.q_in.get(block=True)  # Wait for applet to be started in JVM

    def tearDown(self) -> None:
        if self.q_in.full():
            self.q_in.get()

    def softResetCard(self) -> None:
        self.q_out.put((CommandType.SOFT_RESET, None))
        self.q_in.get(block=True)


class FakeSCConnection:
    q_in: Queue
    q_out: Queue

    def __init__(self, q_in: Queue, q_out: Queue):
        self.q_in = q_in
        self.q_out = q_out

    def connect(self):
        pass

    def disconnect(self):
        pass

    def transmit(self, b, protocol=None):
        self.q_out.put((CommandType.DIRECT_COMMUNICATE, b))
        response = self.q_in.get(block=True)

        sw1 = (response[-2] + 256) % 256
        sw2 = (response[-1] + 256) % 256
        data = [(x + 256) % 256 for x in response[:-2]]

        return list(data), sw1, sw2


class CTAPTestCase(JCardSimTestCase, abc.ABC):
    VIRTUAL_DEVICE_NAME = "Virtual PCD"
    device: CtapPcscDevice
    ctap2: Ctap2
    client_data: bytes
    rp_id: str
    basic_makecred_params: dict[str, Any]

    def setUp(self, install_params: Optional[bytes] = None) -> None:
        self.basic_makecred_params = {
            "rp": {},
            "user": {},
            "key_params": [
                {
                    "type": "public-key",
                    "alg": ES256.ALGORITHM
                }
            ],
        }
        if install_params is None:
            install_params = bytes([0x01])
        super().setUp(install_params)
        if self.PCSC_MODE:
            devs = list(CtapPcscDevice.list_devices(self.VIRTUAL_DEVICE_NAME))
            assert 1 == len(devs)
            self.device = devs[0]
        else:
            self.device = CtapPcscDevice(
                FakeSCConnection(self.q_in, self.q_out),
                'fake_device'
            )

        self.ctap2 = Ctap2(self.device)
        self.ctap1 = Ctap1(self.device)
        self.client_data = self.get_random_client_data()
        self.basic_makecred_params["client_data_hash"] = self.client_data
        rpid_length = random.randint(1, 16)
        self.rp_id = secrets.token_hex(rpid_length)
        self.basic_makecred_params['rp']['id'] = self.rp_id
        userid_length = random.randint(1, 64)
        self.basic_makecred_params['user']['id'] = secrets.token_bytes(userid_length)

    @classmethod
    def rp_id_hash(cls, rp_id: str) -> bytes:
        digester = hashes.Hash(hashes.SHA256())
        digester.update(rp_id.encode())
        return digester.finalize()

    @classmethod
    def get_random_client_data(cls) -> bytes:
        return secrets.token_bytes(32)

    def reset(self):
        self.ctap2.reset()

    def get_assertion_from_cred(self, cred: Optional[AttestationResponse],
                                rp_id: Optional[str] = None,
                                client_data: Optional[bytes] = None,
                                base_allow_list=None,
                                **kwargs) -> AssertionResponse:
        allow_list = [] if base_allow_list is None else base_allow_list
        if cred is not None:
            allow_list.append({
                "type": "public-key",
                "id": cred.auth_data.credential_data.credential_id
            })
        if rp_id is None:
            rp_id = self.rp_id
        if client_data is None:
            client_data = self.client_data
        return self.ctap2.get_assertion(
            rp_id=rp_id,
            client_data_hash=client_data,
            allow_list=allow_list,
            **kwargs
        )

    def get_assertion(self, rp_id: str, client_data: Optional[bytes] = None):
        return self.get_assertion_from_cred(cred=None, rp_id=rp_id, client_data=client_data)

    def get_high_level_client(self, extensions: Optional[list[Type[Ctap2Extension]]] = None,
                              user_interaction: UserInteraction = None,
                              origin: str = None) -> Fido2Client:
        if extensions is None:
            extensions = []
        if user_interaction is None:
            user_interaction = UserInteraction()
        if origin is None:
            origin = 'https://' + self.rp_id
        return Fido2Client(self.device, origin=origin,
                           extension_types=extensions, user_interaction=user_interaction)

    def get_high_level_make_cred_options(self,
                                         resident_key: ResidentKeyRequirement = ResidentKeyRequirement.DISCOURAGED,
                                         extensions=None, rp_id: Optional[str] = None,
                                         user_id: Optional[bytes] = None) -> PublicKeyCredentialCreationOptions:
        if extensions is None:
            extensions = {}

        if rp_id is None:
            rp_id = self.rp_id

        if user_id is None:
            user_id = self.basic_makecred_params['user']['id']

        return PublicKeyCredentialCreationOptions(
            rp=PublicKeyCredentialRpEntity(
                name="An RP Name",
                id=rp_id
            ),
            user=PublicKeyCredentialUserEntity(
                name="Bob",
                id=user_id
            ),
            challenge=self.client_data,
            pub_key_cred_params=[
                PublicKeyCredentialParameters(
                    type=PublicKeyCredentialType.PUBLIC_KEY,
                    alg=ES256.ALGORITHM
                )
            ],
            extensions=extensions,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=resident_key,
                user_verification=UserVerificationRequirement.DISCOURAGED
            )
        )

    def get_descriptor_from_cred_id(self, cred: bytes) -> PublicKeyCredentialDescriptor:
        return PublicKeyCredentialDescriptor(
            type=PublicKeyCredentialType.PUBLIC_KEY,
            id=cred
        )

    def get_descriptor_from_cred(self, cred: AuthenticatorAttestationResponse) -> PublicKeyCredentialDescriptor:
        return self.get_descriptor_from_cred_id(cred.attestation_object.auth_data.credential_data.credential_id)

    def get_descriptor_from_ll_cred(self, cred: AttestationResponse) -> PublicKeyCredentialDescriptor:
        return self.get_descriptor_from_cred_id(cred.auth_data.credential_data.credential_id)

    def get_high_level_assertion_opts_from_cred(self, cred: Optional[AuthenticatorAttestationResponse] = None,
                                                client_data: Optional[bytes] = None, rp_id: Optional[str] = None,
                                                extensions: Optional[
                                                    dict[str, Any]] = None,
                                                user_verification: Optional[UserVerificationRequirement] = None) -> PublicKeyCredentialRequestOptions:
        if extensions is None:
            extensions = {}
        if client_data is None:
            client_data = self.client_data
        if rp_id is None:
            rp_id = self.rp_id
        assertion_allow_credentials = []
        if cred is not None:
            assertion_allow_credentials = [
                self.get_descriptor_from_cred(cred)
            ]
        if user_verification is None:
            user_verification = UserVerificationRequirement.DISCOURAGED
        return PublicKeyCredentialRequestOptions(
            challenge=client_data,
            rp_id=rp_id,
            allow_credentials=assertion_allow_credentials,
            user_verification=user_verification,
            extensions=extensions
        )


class BasicAttestationTestCase(CTAPTestCase):
    VENDOR_COMMAND_SWITCH_ATT = 0x46

    public_key: EllipticCurvePublicKey
    ca_public_key: EllipticCurvePublicKey
    aaguid: bytes
    cert: bytes

    def install_attestation_cert(self):
        self.ctap2.send_cbor(
            self.VENDOR_COMMAND_SWITCH_ATT,
            args(self.gen_attestation_cert())
        )

    def _short_to_bytes(self, b: int) -> list[int]:
        return [(b & 0xFF00) >> 8, b & 0x00FF]

    def get_x509_certs(self, private_key: EllipticCurvePrivateKey, name: Optional[str] = None,
                       country: Optional[str] = "US", org: Optional[str] = "ACME") -> list[bytes]:
        self.public_key = private_key.public_key()

        if name is None:
            name = secrets.token_hex(4)

        now = datetime.now()

        ca_privkey = ec.generate_private_key(ec.SECP256R1())
        ca_pubkey = ca_privkey.public_key()
        self.ca_public_key = ca_pubkey

        ca_name = x509.Name([
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
            x509.NameAttribute(NameOID.COMMON_NAME, "AuthCA"),
        ])
        ca_cert_bytes = (
            x509.CertificateBuilder()
            .subject_name(ca_name)
            .issuer_name(ca_name)
            .serial_number(x509.random_serial_number())
            .public_key(ca_pubkey)
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=3650))
            .sign(private_key=ca_privkey, algorithm=hashes.SHA256())
            .public_bytes(Encoding.DER)
        )

        this_cert_name = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, country),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, org),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "Authenticator Attestation"),
            x509.NameAttribute(NameOID.COMMON_NAME, name),
        ])

        authenticator_cert_bytes = (
            x509.CertificateBuilder()
                .subject_name(this_cert_name)
                .issuer_name(ca_name)
                .serial_number(x509.random_serial_number())
                .public_key(self.public_key)
                .not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=3650))
                .add_extension(
                    x509.BasicConstraints(ca=False, path_length=None), critical=True,
                )
                #.add_extension(x509.UnrecognizedExtension(
                #    x509.ObjectIdentifier("1.3.6.1.4.1.45724.1.1.4"),
                #    value= < DER bytes >
                #))
                .sign(private_key=ca_privkey, algorithm=hashes.SHA256())
                .public_bytes(Encoding.DER)
        )
        return [authenticator_cert_bytes, ca_cert_bytes]

    def gen_attestation_cert(self, cert_bytes: Optional[list[bytes]] = None, name: Optional[str] = None,
                             aaguid: Optional[bytes] = None) -> bytes:
        if aaguid is None:
            aaguid = secrets.token_bytes(16)

        self.aaguid = aaguid
        private_key = ec.generate_private_key(ec.SECP256R1())

        if cert_bytes is None:
            cert_bytes = self.get_x509_certs(private_key, name)

        num_certs = len(cert_bytes)
        self.cert = cert_bytes[0]
        cert_cbor_bytes = [0x80 + num_certs]
        for cert_data in cert_bytes:
            cert_len = len(cert_data)
            if cert_len <= 23:
                cert_len_cbor = [0x40 + cert_len]
            elif cert_len <= 255:
                cert_len_cbor = [0x58, cert_len]
            elif cert_len <= 65535:
                cert_len_cbor = [0x59] + self._short_to_bytes(cert_len)
            else:
                raise NotImplementedError()
            cert_cbor_bytes += cert_len_cbor
            cert_cbor_bytes += cert_data
        cert_cbor = bytes(cert_cbor_bytes)

        s = private_key.private_numbers().private_value
        private_bytes = s.to_bytes(length=32)
        self.assertEqual(32, len(private_bytes))
        cbor_len_bytes = bytes(self._short_to_bytes(len(cert_cbor)))
        res = self.aaguid + private_bytes + cbor_len_bytes + cert_cbor
        return res


class FixedPinUserInteraction(UserInteraction):
    pin: str

    def __init__(self, pin: str):
        self.pin = pin

    def request_pin(
            self, permissions: ClientPin.PERMISSION, rp_id: Optional[str]
    ) -> Optional[str]:
        return self.pin


class CredManagementBaseTestCase(CTAPTestCase, abc.ABC):

    PERMISSION_CRED_MGMT = 4
    pin: str

    def setUp(self, install_params: Optional[bytes] = None) -> None:
        super().setUp(install_params)

        self.pin = secrets.token_hex(10)
        ClientPin(self.ctap2).set_pin(self.pin)

    def get_credential_management(self, permissions: Optional[int] = None) -> CredentialManagement:
        if permissions is None:
            permissions = self.PERMISSION_CRED_MGMT

        client = self.get_high_level_client(
            user_interaction=FixedPinUserInteraction(self.pin),
        )
        # noinspection PyTypeChecker
        be: _Ctap2ClientBackend = client._backend
        token = be._get_token(ClientPin(self.ctap2), permissions=permissions,
                              rp_id=None, event=None, on_keepalive=None, allow_internal_uv=False)
        return CredentialManagement(self.ctap2, pin_uv_protocol=PinProtocolV2(), pin_uv_token=token)
