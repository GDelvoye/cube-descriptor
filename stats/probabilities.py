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


def probability_distribution_by_slots(slots):
    distribution = {0: 1.0}
    for slot in slots:
        if isinstance(slot, dict):
            slot_distribution = slot
        else:
            population_size, success_count, sample_size = slot
            slot_distribution = {
                hits: probability_exactly(population_size, success_count, sample_size, hits)
                for hits in range(0, min(success_count, sample_size) + 1)
            }
        distribution = {
            current_hits + slot_hits: current_probability * slot_probability
            for current_hits, current_probability in distribution.items()
            for slot_hits, slot_probability in slot_distribution.items()
        }
    return distribution


def probability_at_least_by_slots(slots, minimum_hits):
    return sum(
        probability for hits, probability in probability_distribution_by_slots(slots).items() if hits >= minimum_hits
    )


def probability_exactly_by_slots(slots, exact_hits):
    return probability_distribution_by_slots(slots).get(exact_hits, 0.0)


def probability_between_by_slots(slots, minimum_hits, maximum_hits):
    if maximum_hits < minimum_hits:
        return 0.0
    return sum(
        probability
        for hits, probability in probability_distribution_by_slots(slots).items()
        if minimum_hits <= hits <= maximum_hits
    )


def _valid_inputs(population_size, success_count, sample_size):
    return population_size > 0 and 0 <= success_count <= population_size and 0 <= sample_size <= population_size
