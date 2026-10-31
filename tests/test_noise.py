from workcell.ir import Block, Instruction
from workcell.live.noise import NoiseParams, actual_block, actual_duration


def test_deterministic_and_order_independent():
    p = NoiseParams(seed=7)
    a = [actual_duration(p, "pcr-0/12", 1, 4020) for _ in range(3)]
    assert a[0] == a[1] == a[2]
    assert actual_duration(p, "pcr-0/12", 1, 4020) != actual_duration(NoiseParams(seed=8), "pcr-0/12", 1, 4020) \
        or actual_duration(p, "pcr-0/13", 1, 4020) != a[0]


def test_zero_stays_zero_and_no_noise_is_identity():
    assert actual_duration(NoiseParams(seed=1), "x/00", 0, 0) == 0
    assert actual_duration(NoiseParams(noise=0, delay_prob=0), "x/00", 3, 4020) == 4020


def test_distribution_shape():
    p = NoiseParams(seed=3)
    xs = [actual_duration(p, f"b/{i:04d}", 0, 1000) for i in range(5000)]
    longs = sum(x >= 2000 for x in xs) / len(xs)
    assert 0.01 < longs < 0.03
    normal = sorted(x for x in xs if x < 2000)
    assert 930 < normal[len(normal) // 2] < 1070 and min(xs) >= 0


def test_actual_block_matches_per_instruction_draws():
    p = NoiseParams(seed=5)
    block = Block(id="b/01", workflow="w", kind="op", label="l",
                  instructions=[Instruction("inst", "op", {}, d) for d in (10, 0, 300)],
                  moves=[], plates=set(), deps=[])
    assert actual_block(p, block) == [actual_duration(p, "b/01", i, d) for i, d in enumerate((10, 0, 300))]
