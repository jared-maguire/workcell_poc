"""Seeded actual-duration sampler: planned seconds -> what the instruction really takes."""
from __future__ import annotations

import random
from dataclasses import dataclass

from workcell.ir import Block


@dataclass(frozen=True)
class NoiseParams:
    seed: int = 0
    noise: float = 0.05                       # sigma of the relative Normal
    delay_prob: float = 0.02                  # chance of a multiplicative delay
    delay_range: tuple[float, float] = (2.0, 5.0)


def actual_duration(params: NoiseParams, block_id: str, index: int, planned: int) -> int:
    """A draw depends only on (seed, block_id, index), so it is order-independent."""
    rng = random.Random(f"{params.seed}:{block_id}:{index}")
    if rng.random() < params.delay_prob:
        value = planned * rng.uniform(*params.delay_range)
    else:
        value = planned * (1 + rng.gauss(0, params.noise))
    return max(0, round(value))


def actual_block(params: NoiseParams, block: Block) -> list[int]:
    return [actual_duration(params, block.id, i, ins.duration)
            for i, ins in enumerate(block.instructions)]
