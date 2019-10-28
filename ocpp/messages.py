""" Module containing classes that model the several OCPP messages types. It
also contain some helper functions for packing and unpacking messages.  """
import os
import json
from dataclasses import asdict, is_dataclass

from jsonschema import validate
from jsonschema.exceptions import ValidationError as SchemaValidationError

from ocpp.v16.enums import MessageType
from ocpp.exceptions import (OCPPError, FormatViolationError,
                             PropertyConstraintViolationError,
                             ProtocolError, ValidationError,
                             UnknownCallErrorCodeError)

_schemas = {}


def unpack(msg):
    """
    Unpacks a message into either a Call, CallError or CallResult.
    """
    try:
        msg = json.loads(msg)
    except json.JSONDecodeError as e:
        raise FormatViolationError(f'Message is not valid JSON: {e}')

    if not isinstance(msg, list):
        raise ProtocolError("OCPP message hasn't the correct format. It "
                            f"should be a list, but got {type(msg)} instead")

    for cls in [Call, CallResult, CallError]:
        try:
            if msg[0] == cls.message_type_id:
                return cls(*msg[1:])
        except IndexError:
            raise ProtocolError("Message doesn\'t contain MessageTypeID")

    raise PropertyConstraintViolationError(f"MessageTypeId '{msg[0]}' isn't "
                                           "valid")


def pack(msg):
    """
    Returns the JSON representation of a Call, CallError or CallResult.

    It just calls the 'to_json()' method of the message. But it is here mainly
    to complement the 'unpack' function of this module.
    """
    return msg.to_json()


def get_schema(message_type_id, action, ocpp_version):
    """
    Read schema from disk and return in. Reads will be cached for performance
    reasons.

    """
    if ocpp_version == "1.6":
        schemas_dir = "v16"
    elif ocpp_version == "2.0":
        schemas_dir = "v20"
    else:
        raise ValueError

    schema_name = action
    if message_type_id == MessageType.CallResult:
        schema_name += 'Response'
    elif message_type_id == MessageType.Call:
        if ocpp_version == "2.0":
            schema_name += 'Request'

    if ocpp_version == "2.0":
        schema_name += '_v1p0'

    dir,  _ = os.path.split(os.path.realpath(__file__))
    relative_path = f'{schemas_dir}/schemas/{schema_name}.json'
    path = os.path.join(dir, relative_path)

    if relative_path in _schemas:
        return _schemas[relative_path]

    # The JSON schemas for OCPP 2.0 start with a byte order mark (BOM)
    # character. If no encoding is given, reading the schema would fail with:
    #
    #     Unexpected UTF-8 BOM (decode using utf-8-sig):
    with open(path, 'r', encoding='utf-8-sig') as f:
        data = f.read()
        _schemas[relative_path] = json.loads(data)

    return _schemas[relative_path]


def validate_payload(message, ocpp_version):
    """ Validate the payload of the message using JSON schemas. """
    if type(message) not in [Call, CallResult]:
        raise ValidationError("Payload can't be validated because message "
                              f"type. It's '{type(message)}', but it should "
                              "be either 'Call'  or 'CallResult'.")

    try:
        schema = get_schema(
            message.message_type_id, message.action, ocpp_version
        )
    except (OSError, json.JSONDecodeError) as e:
        raise ValidationError("Failed to load validation schema for action "
                              f"'{message.action}': {e}")

    if message.action in ['SetChargingProfile', 'RemoteStartTransaction']:
        # todo: special actions
        pass

    try:
        validate(message.payload, schema)
    except SchemaValidationError as e:
        raise ValidationError(f"Payload '{message.payload} for action "
                              f"'{message.action}' is not valid: {e}")


class Call:
    """ A Call is a type of message that initiate a request/response sequence.
    Both central systems and charge points can send this message.

    From the specification:

        A Call always consists of 4 elements: The standard elements
        MessageTypeId and UniqueId, a specific Action that is required on the
        other side and a payload, the arguments to the Action. The syntax of a
        call looks like this:

            [<MessageTypeId>, "<UniqueId>", "<Action>", {<Payload>}]

        ...

        For example, a BootNotification request could look like this:

            [2,
             "19223201",
             "BootNotification",
             {
              "chargePointVendor": "VendorX",
              "chargePointModel": "SingleSocketCharger"
             }
            ]
    """
    message_type_id = 2

    def __init__(self, unique_id, action, payload):
        self.unique_id = unique_id
        self.action = action
        self.payload = payload

        if is_dataclass(payload):
            self.payload = asdict(payload)

    def to_json(self):
        """ Return a valid JSON representation of the instance. """
        return json.dumps([
            self.message_type_id,
            self.unique_id,
            self.action,
            self.payload,
        ])

    def create_call_result(self, payload):
        call_result = CallResult(self.unique_id, payload)
        call_result.action = self.action
        return call_result

    def create_call_error(self, exception):
        error_code = "InternalError"
        error_description = "An unexpected error occured."
        error_details = {}

        if isinstance(exception, OCPPError):
            error_code = exception.code
            error_description = exception.description
            error_details = exception.details

        return CallError(
            self.unique_id,
            error_code,
            error_description,
            error_details,
        )

    def __repr__(self):
        return f"<Call - unique_id={self.unique_id}, action={self.action}, " \
               f"payload={self.payload}>"


class CallResult:
    """
    A CallResult is a message indicating that a Call has been handled
    succesfully.

    From the specification:

        A CallResult always consists of 3 elements: The standard elements
        MessageTypeId and UniqueId and apayload, containing the response to the
        Action in the original Call. The syntax of a call looks like this:

            [<MessageTypeId>, "<UniqueId>", {<Payload>}]

        ...

        For example, a BootNotification response could look like this:

            [3,
             "19223201",
             {
              "status":"Accepted",
              "currentTime":"2013-02-01T20:53:32.486Z",
              "heartbeatInterval":300
             }
            ]

    """
    message_type_id = 3

    def __init__(self, unique_id, payload, action=None):
        self.unique_id = unique_id
        self.payload = payload

        # Strictly speaking no action is required in a CallResult. But in order
        # to validate the message it is needed.
        self.action = action

    def to_json(self):
        return json.dumps([
            self.message_type_id,
            self.unique_id,
            self.payload,
        ])

    def __repr__(self):
        return f"<CallResult - unique_id={self.unique_id}, " \
               f"action={self.action}, " \
               f"payload={self.payload}>"


class CallError:
    """
    A CallError is a response to a Call that indicates an error.

    From the specification:

        CallError always consists of 5 elements: The standard elements
        MessageTypeId and UniqueId, an errorCode string, an errorDescription
        string and an errorDetails object.

        The syntax of a call looks like this:

            [<MessageTypeId>, "<UniqueId>", "<errorCode>", "<errorDescription>", {<errorDetails>}] # noqa
    """
    message_type_id = 4

    def __init__(self, unique_id, error_code, error_description,
                 error_details=None):
        self.unique_id = unique_id
        self.error_code = error_code
        self.error_description = error_description
        self.error_details = error_details

    def to_json(self):
        return json.dumps([
            self.message_type_id,
            self.unique_id,
            self.error_code,
            self.error_description,
            self.error_details,
        ])

    def to_exception(self):
        """ Return the exception that corresponds to the CallError. """
        for error in OCPPError.__subclasses__():
            if error.code == self.error_code:
                return error(
                    description=self.error_description,
                    details=self.error_details
                )

        raise UnknownCallErrorCodeError("Error code '%s' is not defined by the"
                                        " OCPP specification", self.error_code)

    def __repr__(self):
        return f"<CallError - unique_id={self.unique_id}, " \
               f"error_code={self.error_code}, " \
               f"error_description={self.error_description}, " \
               f"error_details={self.error_details}>"