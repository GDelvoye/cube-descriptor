from math import comb


def probability_exactly(population_size, success_count, sample_size, hits):
    if not _valid_inputs(population_size, success_count, sample_size) or hits < 0:
        return 0.0
    if hits > success_count or hits > sample_size or sample_size - hits > population_size - success_count:
        return 0.0
    return (
        comb(success_count, hits)
        * comb(population_size - success_count, sample_size - hits)
        / comb(population_size, sample_size)
    )


def probability_at_least(population_size, success_count, sample_size, minimum_hits):
    if not _valid_inputs(population_size, success_count, sample_size):
        return 0.0
    max_hits = min(success_count, sample_size)
    return sum(
        probability_exactly(population_size, success_count, sample_size, hits)
        for hits in range(minimum_hits, max_hits + 1)
    )


def probability_between(population_size, success_count, sample_size, minimum_hits, maximum_hits):
    if maximum_hits < minimum_hits or not _valid_inputs(population_size, success_count, sample_size):
        return 0.0
    max_hits = min(maximum_hits, success_count, sample_size)
    return sum(
        probability_exactly(population_size, success_count, sample_size, hits)
        for hits in range(minimum_hits, max_hits + 1)
    )


def _valid_inputs(population_size, success_count, sample_size):
    return population_size > 0 and 0 <= success_count <= population_size and 0 <= sample_size <= population_size
