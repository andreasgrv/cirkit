from collections import defaultdict, deque
from dataclasses import field, dataclass
from enum import Enum, auto
from typing import Dict, Iterator, List, Optional, Set, Callable, Tuple, Any

from cirkit.symbolic.symb_layers import (
    SymbInputLayer,
    SymbLayer,
    SymbMixingLayer,
    SymbProdLayer,
    SymbSumLayer,
)
from cirkit.templates.region_graph import PartitionNode, RegionGraph, RegionNode, RGNode
from cirkit.utils import Scope


class SymbCircuitOperator(Enum):
    """Types of symbolic operations on circuits."""

    INTEGRATION = auto()
    DIFFERENTIATION = auto()
    MULTIPLICATION = auto()


@dataclass(frozen=True)
class SymbCircuitOperation:
    """The symbolic operation applied on a SymbCircuit."""

    operator: SymbCircuitOperator
    operands: Tuple['SymbCircuit', ...]
    metadata: Dict[str, Any] = field(default_factory=dict)


class SymbCircuit:
    """The symbolic representation of a symbolic circuit."""

    def __init__(
        self,
        scope: Scope,
        layers: List[SymbLayer],
        in_layers: Dict[SymbLayer, List[SymbLayer]],
        /,
        *,
        operation: Optional[SymbCircuitOperation] = None,
    ) -> None:
        self.operation = operation
        self.num_vars = len(scope)
        self._layers = layers
        self._in_layers = in_layers

    @classmethod
    def from_region_graph(
        cls,
        region_graph: RegionGraph,
        input_factory: Callable[[Scope, int, int], SymbInputLayer],
        sum_factory: Callable[[Scope, int], SymbSumLayer],
        prod_factory: Callable[[Scope, int], SymbProdLayer],
        num_channels: int = 1,
        num_input_units: int = 1,
        num_sum_units: int = 1,
        num_classes: int = 1
    ) -> "SymbCircuit":
        layers: List[SymbLayer] = []
        in_layers: Dict[SymbLayer, List[SymbLayer]] = {}
        rgn_to_layers: Dict[RGNode, SymbLayer] = {}

        # Loop through the region graph nodes, which are already sorted in a topological ordering
        for rgn in region_graph.nodes:
            if isinstance(rgn, RegionNode) and not rgn.inputs:  # Input region node
                input_sl = input_factory(rgn.scope, num_input_units, num_channels)
                num_sum_units = num_classes if rgn in region_graph.output_nodes else num_sum_units
                sum_sl = sum_factory(rgn.scope, num_sum_units)
                layers.append(input_sl)
                layers.append(sum_sl)
                in_layers[sum_sl] = [input_sl]
                rgn_to_layers[rgn] = sum_sl
            elif isinstance(rgn, PartitionNode):  # Partition node
                prod_sl = prod_factory(rgn.scope, num_sum_units)
                layers.append(prod_sl)
                in_layers[prod_sl] = [rgn_to_layers[rgn_in] for rgn_in in rgn.inputs]
                rgn_to_layers[rgn] = prod_sl
            elif isinstance(rgn, RegionNode):  # Inner region node
                sum_inputs = [rgn_to_layers[rgn_in] for rgn_in in rgn.inputs]
                num_sum_units = num_classes if rgn in region_graph.output_nodes else num_sum_units
                if len(sum_inputs) == 1:  # Region node being partitioned in one way
                    sum_sl = sum_factory(rgn.scope, num_sum_units)
                else:  # Region node being partitioned in multiple way -> add "mixing" layer
                    sum_sl = SymbMixingLayer(rgn.scope, num_sum_units)
                layers.append(sum_sl)
                in_layers[sum_sl] = sum_inputs
                rgn_to_layers[rgn] = sum_sl
            else:
                # NOTE: In the above if/elif, we made all conditions explicit to make it more
                #       readable and also easier for static analysis inside the blocks. Yet the
                #       completeness cannot be inferred and is only guaranteed by larger picture.
                #       Also, should anything really go wrong, we will hit this guard statement
                #       instead of going into a wrong branch.
                assert False, "Region graph nodes must be either region or partition nodes"
        return cls(region_graph.scope, layers, in_layers)

    #######################################    Layer views    ######################################
    # These are iterable views of the nodes in the SymbC, and the topological order is guaranteed
    # (by a stronger ordering). For efficiency, all these views are iterators (implemented as a
    # container iter or a generator), so that they can be chained for iteration without
    # instantiating intermediate containers.

    @property
    def layers(self) -> Iterator[SymbLayer]:
        """All layers in the circuit."""
        return iter(self._layers)

    @property
    def input_layers(self) -> Iterator[SymbInputLayer]:
        """Input layers of the circuit."""
        return (layer for layer in self.layers if isinstance(layer, SymbInputLayer))

    @property
    def sum_layers(self) -> Iterator[SymbSumLayer]:
        """Sum layers in the circuit, which are always inner layers."""
        return (layer for layer in self.layers if isinstance(layer, SymbSumLayer))

    @property
    def product_layers(self) -> Iterator[SymbProdLayer]:
        """Product layers in the circuit, which are always inner layers."""
        return (layer for layer in self.layers if isinstance(layer, SymbProdLayer))

    @property
    def inner_layers(self) -> Iterator[SymbLayer]:
        """Inner (non-input) layers in the circuit."""
        return (layer for layer in self.layers if self._in_layers[layer])


def pipeline_topological_ordering(roots: Set[SymbCircuit]) -> List[SymbCircuit]:
    # Initialize the number of incomings edges for each node
    in_symb_circuits: List[SymbCircuit] = []
    num_incomings: Dict[SymbCircuit, int] = defaultdict(int)
    outgoings: Dict[SymbCircuit, List[SymbCircuit]] = defaultdict(list)

    # BFS
    seen, queue = set(roots), deque(roots)
    while not queue:
        sc = queue.popleft()
        if sc.operation is None:
            in_symb_circuits.append(sc)
            continue
        symb_operands = sc.operation.operands
        for op in symb_operands:
            num_incomings[sc] += 1
            outgoings[op].append(sc)
            if op not in seen:
                seen.add(op)
                queue.append(op)

    # Kahn's algorithm
    ordering: List[SymbCircuit] = []
    to_visit = deque(in_symb_circuits)
    while to_visit:
        sc = to_visit.popleft()
        ordering.append(sc)
        for out_sc in outgoings[sc]:
            num_incomings[out_sc] -= 1
            if num_incomings[out_sc] == 0:
                to_visit.append(out_sc)

    # Check for possible cycles in the pipeline
    for sc, n in num_incomings.keys():
        if n != 0:
            raise ValueError("The given pipeline of symbolic circuit has at least one cycle")
    return ordering
