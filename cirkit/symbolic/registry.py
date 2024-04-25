import itertools
from contextlib import AbstractContextManager
from contextvars import Token
from types import TracebackType
from typing import Callable, Dict, Iterable, Optional, Tuple, Type

from cirkit.symbolic.functional import OPERATOR_REGISTRY
from cirkit.symbolic.layers import AbstractLayerOperator, Layer
from cirkit.symbolic.operators import (
    DEFAULT_COMMUTATIVE_OPERATORS,
    DEFAULT_OPERATOR_RULES,
    CircuitBlock,
)

LayerOperatorSign = Tuple[Type[Layer], ...]
LayerOperatorFunc = Callable[..., CircuitBlock]
LayerOperatorSpecs = Dict[LayerOperatorSign, LayerOperatorFunc]


class OperatorNotFound(Exception):
    def __init__(self, op: AbstractLayerOperator):
        super().__init__()
        self._operator = op

    def __repr__(self) -> str:
        return f"Symbolic operator named '{self._operator.name}' not found"


class OperatorSignatureNotFound(Exception):
    def __init__(self, *signature: Type[Layer]):
        super().__init__()
        self._signature = tuple(signature)

    def __repr__(self) -> str:
        signature_repr = ", ".join(map(lambda cls: cls.__name__, self._signature))
        return f"Symbolic operator for signature ({signature_repr}) not found"


class OperatorRegistry(AbstractContextManager):
    def __init__(self):
        # The symbolic operator rule specifications, for each symbolic operator over layers
        self._rules: Dict[AbstractLayerOperator, LayerOperatorSpecs] = {}

        # The token used to restore the operator registry context
        self._token: Optional[Token[OperatorRegistry]] = None

    @classmethod
    def from_default_rules(cls) -> "OperatorRegistry":
        registry = cls()
        for op, funcs in DEFAULT_OPERATOR_RULES.items():
            for f in funcs:
                registry.register_rule(op, f, commutative=op in DEFAULT_COMMUTATIVE_OPERATORS)
        return registry

    @property
    def operators(self) -> Iterable[AbstractLayerOperator]:
        return self._rules.keys()

    def __enter__(self) -> "OperatorRegistry":
        self._token = OPERATOR_REGISTRY.set(self)
        return self

    def __exit__(
        self,
        __exc_type: Optional[Type[BaseException]],
        __exc_value: Optional[BaseException],
        __traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        OPERATOR_REGISTRY.reset(self._token)
        self._token = None
        return None

    def has_rule(self, op: AbstractLayerOperator, *signature: Type[Layer]) -> bool:
        if op not in self._rules:
            return False
        op_rules = self._rules[op]
        known_signatures = op_rules.keys()
        if signature in known_signatures:
            return True
        for s in known_signatures:
            if len(signature) != len(s):
                continue
            if all(issubclass(x[0], x[1]) for x in zip(signature, s)):
                return True
        return False

    def retrieve_rule(
        self, op: AbstractLayerOperator, *signature: Type[Layer]
    ) -> LayerOperatorFunc:
        if op not in self._rules:
            raise OperatorNotFound(op)
        op_rules = self._rules[op]
        known_signatures = op_rules.keys()
        if signature in known_signatures:
            return op_rules[signature]
        for s, f in op_rules.items():
            if len(signature) != len(s):
                continue
            # TODO: handle more complicated sub-signatures hierarchies obtained via sub-classing layers
            if all(issubclass(x[0], x[1]) for x in zip(signature, s)):
                # Cache rule signature for sub-signatures
                self._register_rule(
                    op, f, signature, commutative=op in DEFAULT_COMMUTATIVE_OPERATORS
                )
                return f
        raise OperatorSignatureNotFound(*signature)

    def register_rule(
        self,
        op: AbstractLayerOperator,
        func: LayerOperatorFunc,
        commutative: Optional[bool] = None,
    ):
        args = func.__annotations__
        arg_names = args.keys()
        if "return" not in arg_names or not issubclass(args["return"], CircuitBlock):
            raise ValueError("The function is not an operator over symbolic layers")
        del args["return"]
        arg_names = list(args.keys())
        arg_types = [args[a] for a in arg_names]
        arg_layer_types = list(filter(lambda x: issubclass(x[1], Layer), enumerate(arg_types)))
        arg_layer_types_locs, signature = list(zip(*arg_layer_types))
        if arg_layer_types_locs != list(range(len(arg_layer_types_locs))):
            raise ValueError(
                "The layer operands should be the first arguments of the operator rule function"
            )
        return self._register_rule(op, func, signature, commutative=commutative)

    def _register_rule(
        self,
        op: AbstractLayerOperator,
        func: LayerOperatorFunc,
        signature: LayerOperatorSign,
        commutative: Optional[bool] = None,
    ):
        if len(signature) == 2:
            # Binary operator found (special case as to deal with commutative operators)
            self._rules[op][signature] = func
            if commutative:
                lhs_cls, rhs_cls = signature
                if lhs_cls != rhs_cls:
                    self._rules[op][(rhs_cls, lhs_cls)] = lambda rhs, lhs: func(lhs, rhs)
        else:  # n-ary operator
            self._rules[op][signature] = func
