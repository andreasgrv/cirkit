import os
from contextlib import AbstractContextManager
from contextvars import ContextVar, Token
from types import TracebackType
from typing import IO, Any, Iterable, Optional, Type, Union

from cirkit.backend.base import SUPPORTED_BACKENDS, AbstractCompiler, LayerCompilationFunc, \
    ParameterCompilationFunc
from cirkit.symbolic.functional import differentiate, integrate, multiply
from cirkit.symbolic.registry import LayerOperatorFunc, OperatorRegistry
from cirkit.symbolic.circuit import Circuit
from cirkit.symbolic.layers import AbstractLayerOperator

# Context variable containing the symbolic operator registry.
# This is updated when entering a pipeline context.
_OPERATOR_REGISTRY: ContextVar[OperatorRegistry] = ContextVar(
    "_OPERATOR_REGISTRY", default=OperatorRegistry()
)


class PipelineContext(AbstractContextManager):
    def __init__(self, backend: str = "torch", **backend_kwargs):
        if backend not in SUPPORTED_BACKENDS:
            raise NotImplementedError(f"Backend '{backend}' is not implemented")
        # Backend specs
        self._backend = backend
        self._backend_kwargs = backend_kwargs

        # Symbolic operator registry (and token for context management)
        self._op_registry = OperatorRegistry()
        self._op_registry_token: Optional[Token[OperatorRegistry]] = None

        # Get the compiler, which is backend-dependent
        self._compiler = retrieve_compiler(backend, **backend_kwargs)

    def __getitem__(self, sc: Circuit) -> Any:
        return self._compiler.get_compiled_circuit(sc)

    def __enter__(self) -> "PipelineContext":
        self._op_registry_token = _OPERATOR_REGISTRY.set(self._op_registry)
        return self

    def __exit__(
        self,
        __exc_type: Optional[Type[BaseException]],
        __exc_value: Optional[BaseException],
        __traceback: Optional[TracebackType],
    ) -> Optional[bool]:
        _OPERATOR_REGISTRY.reset(self._op_registry_token)
        self._op_registry_token = None
        return None

    def save(
        self,
        sym_fp: Union[IO, os.PathLike, str],
        state_fp: Optional[Union[IO, os.PathLike, str]] = None,
    ):
        ...

    @staticmethod
    def load(
        sym_fp: Union[IO, os.PathLike, str], state_fp: Optional[Union[IO, os.PathLike, str]] = None
    ) -> "PipelineContext":
        ...

    def register_operator_rule(self, op: AbstractLayerOperator, func: LayerOperatorFunc):
        self._op_registry.register_rule(op, func)

    def add_layer_compilation_rule(self, func: LayerCompilationFunc):
        self._compiler.add_layer_rule(func)

    def add_parameter_compilation_rule(self, func: ParameterCompilationFunc):
        self._compiler.add_parameter_rule(func)

    def compile(self, sc: Circuit) -> Any:
        return self._compiler.compile(sc)

    def integrate(self, tc: Any, scope: Optional[Iterable[int]] = None) -> Any:
        if not self._compiler.has_symbolic(tc):
            raise ValueError("The given compiled circuit is not known in this pipeline")
        sc = self._compiler.get_symbolic_circuit(tc)
        int_sc = integrate(sc, scope=scope, registry=self._op_registry)
        return self.compile(int_sc)

    def multiply(self, lhs_tc: Any, rhs_tc: Any) -> Any:
        if not self._compiler.has_symbolic(lhs_tc):
            raise ValueError("The given LHS compiled circuit is not known in this pipeline")
        if not self._compiler.has_symbolic(rhs_tc):
            raise ValueError("The given RHS compiled circuit is not known in this pipeline")
        lhs_sc = self._compiler.get_symbolic_circuit(lhs_tc)
        rhs_sc = self._compiler.get_symbolic_circuit(rhs_tc)
        prod_sc = multiply(lhs_sc, rhs_sc, registry=self._op_registry)
        return self.compile(prod_sc)

    def differentiate(self, tc: Any) -> Any:
        if not self._compiler.has_symbolic(tc):
            raise ValueError("The given compiled circuit is not known in this pipeline")
        sc = self._compiler.get_symbolic_circuit(tc)
        diff_sc = differentiate(sc, registry=self._op_registry)
        return self.compile(diff_sc)


def retrieve_compiler(backend: str, **backend_kwargs) -> AbstractCompiler:
    if backend not in SUPPORTED_BACKENDS:
        raise NotImplementedError(f"Backend '{backend}' is not implemented")
    if backend == "torch":
        from cirkit.backend.torch.compiler import TorchCompiler

        return TorchCompiler(**backend_kwargs)
    assert False