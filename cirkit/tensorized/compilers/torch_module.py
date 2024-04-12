from typing import Dict, List, Optional, Set, Tuple, Type, Union, cast

import torch
from torch import Tensor

from cirkit.layers import (
    CategoricalLayer,
    DenseLayer,
    HadamardLayer,
    InnerLayer,
    InputLayer,
    KroneckerLayer,
    Layer,
    MixingLayer,
    SumLayer,
)
from cirkit.layers.input import ConstantLayer
from cirkit.symbolic.symb_circuit import SymbCircuit, pipeline_topological_ordering
from cirkit.symbolic.symb_layers import (
    SymbCategoricalLayer,
    SymbConstantLayer,
    SymbHadamardLayer,
    SymbInputLayer,
    SymbKroneckerLayer,
    SymbLayer,
    SymbMixingLayer,
    SymbProdLayer,
    SymbSumLayer,
)
from cirkit.symbolic.symb_op import SymbOperator
from cirkit.tensorized.compilers import PipelineContext
from cirkit.tensorized.models import TensorizedCircuit
from cirkit.tensorized.reparams import Reparameterization


def compile_pipeline(
    roots: Set[SymbCircuit], reparam: Reparameterization, ctx: Optional[PipelineContext] = None
) -> PipelineContext:
    if ctx is None:  # Create a new pipeline context if compiling a pipeline from scratch
        ctx = PipelineContext()

    # Retrieve the topological ordering of the pipeline
    ordering = pipeline_topological_ordering(roots)

    # Materialize the circuits in the pipeline following the topological ordering
    # The parameters are saved in the input materialized circuits (for now),
    # and also shared across all the materialized circuits within the pipeline
    for symb_circuit in ordering:
        # Check if the circuit in the pipeline has already been materialized
        if symb_circuit in ctx:
            continue
        # Materialize the circuit
        ctx = compile_circuit(symb_circuit, reparam, ctx)
    return ctx


def compile_circuit(
    symb_circuit: SymbCircuit, reparam: Reparameterization, ctx: PipelineContext
) -> PipelineContext:
    # The list of layers
    layers: List[Layer] = []

    # The bookkeeping data structure
    bookkeeping: List[Tuple[List[int], Optional[Tensor]]] = []

    # A useful map from symbolic layers to layer id (indices for the list of layers)
    symb_layers_map: Dict[SymbLayer, int] = {}

    # Construct the bookkeeping data structure while compiling layers
    for (
        sl
    ) in symb_circuit.layers:  # Assuming the layers are already sorted in a topological ordering
        if isinstance(sl, SymbInputLayer):
            layer = compile_input_layer(symb_circuit, sl, ctx)
            bookkeeping_entry = ([], torch.tensor([list(sl.scope)]))
            bookkeeping.append(bookkeeping_entry)
        else:
            assert isinstance(sl, (SymbSumLayer, SymbProdLayer))
            layer = compile_inner_layer(symb_circuit, sl, ctx, reparam=reparam)
            bookkeeping_entry = ([symb_layers_map[isl] for isl in sl.inputs], None)
            bookkeeping.append(bookkeeping_entry)
        layer_id = len(layers)
        symb_layers_map[sl] = layer_id
        layers.append(layer)

    # Append a last bookkeeping entry with the info to extract the (possibly multiple) outputs
    output_indices = [symb_layers_map[sl] for sl in symb_circuit.output_layers]
    bookkeeping_entry = (output_indices, None)
    bookkeeping.append(bookkeeping_entry)

    # Construct the tensorized circuit object, and update the pipeline context
    circuit = TensorizedCircuit(symb_circuit, layers, bookkeeping)
    ctx.register_materialized_circuit(symb_circuit, circuit, symb_layers_map)
    return ctx


def compile_input_layer(
    symb_circuit: SymbCircuit, symb_layer: SymbInputLayer, ctx: PipelineContext
) -> InputLayer:
    # Registry mapping symbolic input layers to executable layers classes
    materialize_input_registry: Dict[Type[SymbInputLayer], Type[InputLayer]] = {
        SymbConstantLayer: ConstantLayer,
        SymbCategoricalLayer: CategoricalLayer,
    }

    layer_cls = materialize_input_registry[type(symb_layer)]

    symb_layer_operation = symb_layer.operation
    if symb_layer_operation is None:
        return layer_cls(
            num_input_units=symb_layer.num_channels,
            num_output_units=symb_layer.num_units,
            arity=len(symb_layer.scope),
            reparam=layer_cls.default_reparam(),
            **symb_layer.kwargs,
        )

    if symb_layer_operation.operator == SymbOperator.INTEGRATION:
        symb_circuit_op = symb_circuit.operation.operands[0]
        symb_layer_op = symb_layer_operation.operands[0]
        layer_op: InputLayer = cast(
            InputLayer, ctx.get_materialized_layer(symb_circuit_op, symb_layer_op)
        )
        return layer_cls(
            num_input_units=symb_layer.num_channels,
            num_output_units=symb_layer.num_units,
            arity=len(symb_layer.scope),
            reparam=layer_op.reparam,
            **symb_layer.kwargs,
        )

    assert False


def compile_inner_layer(
    symb_circuit: SymbCircuit,
    symb_layer: Union[SymbSumLayer, SymbProdLayer],
    ctx: PipelineContext,
    reparam: Reparameterization,
) -> InnerLayer:
    # Registry mapping symbolic inner layers to executable layer classes
    materialize_inner_registry: Dict[Type[SymbLayer], Type[InnerLayer]] = {
        SymbSumLayer: DenseLayer,
        SymbMixingLayer: MixingLayer,
        SymbHadamardLayer: HadamardLayer,
        SymbKroneckerLayer: KroneckerLayer,
    }

    layer_cls = materialize_inner_registry[type(symb_layer)]

    symb_layer_operation = symb_layer.operation
    if symb_layer_operation is None or not isinstance(symb_layer, SymbSumLayer):
        return layer_cls(
            num_input_units=symb_layer.inputs[0].num_units,
            num_output_units=symb_layer.num_units,
            arity=len(symb_layer.inputs),
            **symb_layer.kwargs,
        )

    if symb_layer_operation.operator == SymbOperator.INTEGRATION:
        symb_circuit_op = symb_circuit.operation.operands[0]
        symb_layer_op = symb_layer_operation.operands[0]
        layer_op: SumLayer = cast(
            SumLayer, ctx.get_materialized_layer(symb_circuit_op, symb_layer_op)
        )
        return layer_cls(
            num_input_units=symb_layer.inputs[0].num_units,
            num_output_units=symb_layer.num_units,
            arity=len(symb_layer.inputs),
            reparam=layer_op.reparam,
        )

    assert False