from workcell.library import pcr


def batch():
    return [pcr(sample=f"S{i+1:04d}", primer=f"P{i % 2 + 1:04d}") for i in range(10)]
