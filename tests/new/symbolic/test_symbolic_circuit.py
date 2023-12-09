# pylint: disable=missing-function-docstring

from cirkit.new.region_graph import QuadTree
from cirkit.new.symbolic import SymbolicInputLayer, SymbolicSumLayer
from cirkit.new.utils import Scope
from tests.new.symbolic.test_utils import get_simple_rg, get_symbolic_circuit_on_rg


def test_symbolic_circuit_simple() -> None:
    rg = get_simple_rg()

    circuit = get_symbolic_circuit_on_rg(rg)

    assert len(list(circuit.layers)) == 4
    # Ignore: SymbolicInputLayer contains Any.
    assert all(
        isinstance(layer, SymbolicInputLayer)  # type: ignore[misc]
        for layer in circuit.input_layers
    )
    # Ignore: SymbolicSumLayer contains Any.
    assert all(
        isinstance(layer, SymbolicSumLayer) for layer in circuit.output_layers  # type: ignore[misc]
    )


def test_symbolic_circuit_qt() -> None:
    rg = QuadTree((4, 4), struct_decomp=True)

    circuit = get_symbolic_circuit_on_rg(rg)

    assert len(list(circuit.layers)) == 46
    assert len(list(circuit.input_layers)) == 16
    assert len(list(circuit.output_layers)) == 1
    assert circuit.scope == Scope(range(16))
