from workcell.compiler import compile_batch
from workcell.planners import make_plan
from workcell.scenarios import scale_specs, scale_workcell
from workcell.sim.executor import execute
from workcell.validate import validate_plan


def test_scale_compiles_and_hotel_completes():
    wc = scale_workcell(stampers=2, thermocyclers=4, scanners=2, samples=20, primers=4, empties=20)
    b = compile_batch(wc, scale_specs(20, primers=4))
    assert b.workflows[0].variables.keys() >= {"?stamp", "?tc0"}
    p = make_plan(b, "greedy_hotel"); assert p.status == "complete" and validate_plan(p, b) == []
    assert execute(p, b).alarm is None
