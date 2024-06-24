import logging
import math
from dataclasses import asdict, fields, is_dataclass
from enum import IntEnum
from typing import Annotated, Any, get_args, get_origin, Dict, List, Optional

from construct import (
    Adapter,
    Array,
    Const,
    Enum,
    Flag,
    IfThenElse,
    Int32ul,
    Padding,
    Pointer,
    Struct,
    Subconstruct,
    this,
)

logger = logging.getLogger('point_one.user_config_loader_utils')


# Used to allow construction from either integer or string representation of enum.
class IntOrStrEnum(IntEnum):
    @classmethod
    def _missing_(cls, value):
        if isinstance(value, str):
            return cls[value]
        return super()._missing_(value)


class AdapterWithDefaults(Adapter):
    def get_default_val(self):
        raise NotImplementedError()


class EnumAdapter(AdapterWithDefaults):
    """!
    @brief Adapter for automatically converting between construct Enum and
           python Enums.

    Usage Example:
    ```{.py}
        class ConfigType(IntEnum):
            FOO = 0
            BAR = 1

        ConfigConstruct = EnumAdapter(ConfigType, Enum(Int32ul, ConfigType))

        UserConfigConstruct = Struct(
            "config_type" / ConfigConstruct,
        )

        data = UserConfigConstruct.build({'config_type': ConfigType.ACTIVE})
        assert ConfigType.ACTIVE == UserConfigConstruct.parse(data).config_type
    ```
    """

    def __init__(self, enum_cls, *args):
        """!
        @brief Create an adapter for (de)serializing Enums.

        @param enum_cls The Enum to adapt.
        """
        super().__init__(*args)
        self.enum_cls = enum_cls

    def _decode(self, obj, context, path):
        return self.enum_cls(int(obj))

    def _encode(self, obj, context, path):
        if isinstance(obj, str):
            return self.enum_cls[obj]
        else:
            return obj

    def get_default_val(self):
        return next(iter(self.enum_cls))


def AutoEnum(construct_cls, enum_cls):
    """!
    @brief Wrapper for @ref EnumAdapter to make its arguments simpler.

    Usage Example:
    ```{.py}
        class ConfigType(IntEnum):
            FOO = 0
            BAR = 1

        UserConfigConstruct = Struct(
            "config_type" / AutoEnum(Int32ul, ConfigType),
        )

        data = UserConfigConstruct.build({'config_type': ConfigType.ACTIVE})
        assert ConfigType.ACTIVE == UserConfigConstruct.parse(data).config_type
    ```
    """
    return EnumAdapter(enum_cls, Enum(construct_cls, enum_cls))


class NamedTupleAdapter(AdapterWithDefaults):
    """!
    @brief Adapter for automatically converting between construct streams and
           NamedTuples with corresponding fields.

    Usage Example:
    ```{.py}
        class VersionTuple(NamedTuple):
            major: int
            minor: int

        VersionRawConstruct = Struct(
            "major" / Int8ul,
            "minor" / Int16ul,
        )

        VersionConstruct = NamedTupleAdapter(VersionTuple, VersionRawConstruct)
        UserConfigConstruct = Struct(
            "version" / VersionConstruct,
            "thing2" / Int32ul,
        )
        UserConfigConstruct.build({'version': VersionTuple(2, 3), 'thing2': 4})
    ```
    """

    def __init__(self, tuple_cls, *args):
        """!
        @brief Create an adapter for (de)serializing NamedTuples.

        @param tuple_cls The NamedTuple to adapt.
        """
        super().__init__(*args)
        self.tuple_cls = tuple_cls

    def _decode(self, obj, context, path):
        # skip _io member
        return self.tuple_cls(*list(obj.values())[1:])

    def _encode(self, obj, context, path):
        return obj._asdict()

    def get_default_val(self):
        return self.tuple_cls()


class DataClassAdapter(AdapterWithDefaults):
    def __init__(self, cls, *args):
        super().__init__(*args)
        self.cls = cls

    def _decode(self, obj, context, path):
        # skip _io member
        return self.cls(*list(obj.values())[1:])

    def _encode(self, obj, context, path):
        if obj is None:
            return asdict(self.cls())
        elif isinstance(obj, dict):
            return obj
        else:
            return asdict(obj)

    def get_default_val(self):
        return self.cls()


class ClassAdapter(AdapterWithDefaults):
    """!
    @brief Adapter for automatically converting between construct streams and
           a class with corresponding fields.

    Usage Example:
    ```{.py}
        class VersionClass:
            def __init__(self, major=0, minor=0):
                self.major = major
                self.minor = minor

        VersionRawConstruct = Struct(
            "major" / Int8ul,
            "minor" / Int16ul,
        )

        VersionConstruct = ClassAdapter(VersionClass, VersionRawConstruct)
        UserConfigConstruct = Struct(
            "version" / VersionConstruct,
            "thing2" / Int32ul,
        )
        UserConfigConstruct.build({'version': VersionClass(2, 3), 'thing2': 4})
    ```
    """

    def __init__(self, cls, *args):
        """!
        @brief Create an adapter for (de)serializing a class.

        @param cls The class to adapt.
        """
        super().__init__(*args)
        self.cls = cls

    def _decode(self, obj, context, path):
        val = self.cls()
        val.__dict__.update(obj)
        return val

    def _encode(self, obj, context, path):
        return obj.__dict__

    def get_default_val(self):
        return self.cls()


# TODO: Fix padding to 4 byte alignment
class OptionalAdapter(AdapterWithDefaults):
    def __init__(self, subcon: Subconstruct):
        optional_subcon = Struct(
            'valid' / Pointer(subcon.sizeof(), Flag),
            "value" / IfThenElse(this.valid, subcon, Const(b'\x00' * subcon.sizeof())),
            Padding(1),
        )
        super().__init__(optional_subcon)

    def _decode(self, obj, context, path):
        return obj['value'] if obj['valid'] else None

    def _encode(self, obj, context, path):
        return {'valid': obj is not None, 'value': obj}

    def get_default_val(self):
        return None


# TODO: Fix padding to 4 byte alignment
class FrozenVectorAdapter(Adapter):
    def __init__(self, max_size, storage_subcon: Subconstruct):
        frozen_vector_subcon = Struct(
            "values" / Array(max_size, storage_subcon),
            "size" / Int32ul,
        )
        self.max_size = max_size
        self.storage_subcon = storage_subcon
        super().__init__(frozen_vector_subcon)

    def _decode(self, obj, context, path):
        return obj['values'][: obj['size']]

    def _encode(self, obj, context, path):
        default_val = (
            self.storage_subcon.get_default_val() if isinstance(self.storage_subcon, AdapterWithDefaults) else 0
        )
        return {'size': len(obj), 'values': obj + [default_val] * (self.max_size - len(obj))}


# Turn fields loaded from JSON into their correct representation.
def _interpret_value(field_type: type, val):
    if field_type == float and isinstance(val, str):
        return math.nan
    elif issubclass(field_type, IntOrStrEnum):
        return field_type(val)
    else:
        return val


# Recursively update the fields in data_class in-place with the corresponding values.
# Only values that aren't none and with keys that match the field names will be used to update data_class.
# Fields that aren't updated will preserve their current values.
#
# Lists (vectors, arrays, etc.) are treated as values and don't have their values merged.
# For example if the original value was `{"arr": [0, 1]}`:
# Merging with a modification of `{"arr": [10]}` would result in `{"arr": [10]}`
#
# To update a value in a list, specify an index is also supported. Use the syntax "key_name/i" where "i" is the index of
# the array to merge changes into.
# For example if the original value was `{"arr": [{"a":0}, {"a":1}]}`:
# Merging with a modification of `{"arr/1": {"a":10}}` would result in `{"arr": [{"a":0}, {"a":10}]}`
#
# Return a dict of entries in values that don't have corresponding fields in data_class.
def update_dataclass_contents(data_class, values: Dict[str, Any]) -> Dict[str, Any]:
    indexed_values = {}
    unused = {}
    field_names = [field.name for field in fields(data_class)]
    # Check if any of the keys contain array indexes, or don't exist in data_class.
    for k, v in dict(values).items():
        split_key = k.split('/')
        if len(split_key) == 1:
            if k not in field_names:
                unused[k] = ''
        elif len(split_key) == 2:
            try:
                actual_key = split_key[0]
                indexed_values[actual_key] = int(split_key[1])
                values[actual_key] = v
                if actual_key not in field_names:
                    unused[actual_key] = ''
            except Exception as e:
                pass

    # When recursing, use this helper function to update the unused keys.
    def _update_unused(key, child_unused):
        if len(child_unused) > 0:
            if key in unused:
                unused[key].update(child_unused)
            else:
                unused[key] = child_unused

    for field in fields(data_class):
        k = field.name
        if k in values and values[k] is not None:
            # Either get the field type directly, or if if a Annotated type (a List with fixed size), get the child
            # type.
            if get_origin(field.type) == Annotated:
                field_type = get_args(field.type)[0]
                field_type_meta = get_args(field.type)[1]
            else:
                field_type = field.type
                field_type_meta = None

            if is_dataclass(field_type):
                _update_unused(k, update_dataclass_contents(getattr(data_class, k), values[k]))
            elif get_origin(field_type) == list:
                # The type of the values in the List.
                list_type = get_args(field_type)[0]
                current_values = getattr(data_class, k)
                # The update to the list is specified with an index `"key_name/1": val`. This is unambiguous and we
                # merge the value with the value currently at this index. An exception is raised if the index doesn't
                # exist.
                if k in indexed_values:
                    i = indexed_values[k]
                    v = values[k]
                    original_key = k + f'/{i}'
                    if i >= len(current_values):
                        raise IndexError(f'{k}/{i} out of range {field_type} size {len(current_values)}.')
                    if is_dataclass(list_type):
                        data_val = current_values[i]
                        _update_unused(original_key, update_dataclass_contents(data_val, v))
                    else:
                        data_val = _interpret_value(list_type, v)
                    current_values[i] = data_val
                # Trying to merge into a list of values. This is interpreted as replacing the current list without
                # merging (treating the array as a value to replace). This is often a surprising result, especially when
                # the entries are themselves complex objects. To merge into a value in an array the update index
                # notation `"key_name/1": val`.
                else:
                    try:
                        if isinstance(values[k], str) or isinstance(values[k], dict):
                            raise TypeError()
                        update_size = len(values[k])
                    except TypeError:
                        raise TypeError(f'Scalar value being used to update list {k}.')

                    # This field encodes a list with fixed size (an array).
                    if field_type_meta is not None:
                        field_list_size = field_type_meta
                        if update_size != field_list_size:
                            raise TypeError(
                                f"Value {k} is a fixed size list. Update value size {update_size} does't match list size {field_list_size}."
                            )

                    loaded_values = []
                    for i, v in enumerate(values[k]):
                        if is_dataclass(list_type):
                            data_val = list_type()
                            _update_unused(k, update_dataclass_contents(data_val, v))
                        else:
                            data_val = _interpret_value(list_type, v)
                        loaded_values.append(data_val)
                    setattr(data_class, k, loaded_values)

            else:
                setattr(data_class, k, _interpret_value(field.type, values[k]))

    return unused


# Used for formatting values for conversion to JSON.
def prepare_dataclass_for_json(obj):
    if is_dataclass(obj):
        return prepare_dataclass_for_json(asdict(obj))
    # Convert NaN floats into 'nan' strings.
    elif isinstance(obj, float) and math.isnan(obj):
        return 'nan'
    # Convert IntEnum into their string representation.
    elif isinstance(obj, IntEnum):
        return obj.name
    elif isinstance(obj, dict):
        return dict((k, prepare_dataclass_for_json(v)) for k, v in obj.items())
    elif isinstance(obj, (list, tuple)):
        return list(map(prepare_dataclass_for_json, obj))
    else:
        return obj
