from __future__ import annotations

import unittest

from express_mesh_project.model import (
    bit_complement_demand,
    ExpressEdge,
    GridGraph,
    cutstress_demand,
    cutstress_bidirectional_demand,
    core_metrics,
    hotspot_demand,
    tornado_demand,
    soc_heterogeneous_demand,
    uniform_demand,
)
from express_mesh_project.placement import (
    edge_latency,
    greedy_placement,
    random_placement,
    validate_placement,
)
from express_mesh_project.candidates import (
    all_candidates,
    candidate_cost,
    candidate_diagnostics,
    choose_candidate,
    RouteExecutionState,
    sequential_reservation_assignment,
    candidate_bundle,
)
from express_mesh_project.run_phase3_measurement_v2 import traffic_mapping_stats
from express_mesh_project.flow_placement import compress_demand, flow_metrics
from express_mesh_project.search_placement_advanced import (
    _curve_throughput_guardrail,
)


class OfflineModelTest(unittest.TestCase):
    def test_length_aware_latency_uses_configured_wire_rate(self):
        self.assertEqual(
            [edge_latency(length, "length-aware", 2)
             for length in (3, 4, 5, 6)],
            [2, 2, 3, 3],
        )
        self.assertEqual(
            [edge_latency(length, "length-aware", 4)
             for length in (3, 4, 5, 8)],
            [1, 1, 2, 2],
        )

    def test_curve_guardrail_checks_every_rate_with_one_relative_tolerance(self):
        incumbent = {"by_traffic": [
            {"traffic": "uniform_random", "rate": 0.4,
             "accepted_throughput": 0.2},
            {"traffic": "uniform_random", "rate": 0.8,
             "accepted_throughput": 0.3},
        ]}
        acceptable = {"by_traffic": [
            {"traffic": "uniform_random", "rate": 0.4,
             "accepted_throughput": 0.1995},
            {"traffic": "uniform_random", "rate": 0.8,
             "accepted_throughput": 0.31},
        ]}
        regressed = {"by_traffic": [
            {"traffic": "uniform_random", "rate": 0.4,
             "accepted_throughput": 0.1994},
            {"traffic": "uniform_random", "rate": 0.8,
             "accepted_throughput": 0.32},
        ]}
        self.assertTrue(_curve_throughput_guardrail(
            acceptable, incumbent, tolerance=0.0025,
        ))
        self.assertFalse(_curve_throughput_guardrail(
            regressed, incumbent, tolerance=0.0025,
        ))

    def test_flow_proxy_splits_demand_and_rewards_useful_capacity(self):
        demand = bit_complement_demand(4)
        mesh = flow_metrics(4, [], demand, rounds=32)
        augmented = flow_metrics(
            4, [ExpressEdge(0, 15, 6, 1)], demand, rounds=32,
        )
        self.assertEqual(mesh.commodity_count, 16)
        self.assertLess(augmented.network_max_load, mesh.network_max_load)
        self.assertGreater(
            augmented.concurrent_flow_proxy, mesh.concurrent_flow_proxy,
        )

    def test_compressed_demand_is_deterministic_and_normalized(self):
        demand = hotspot_demand(8)
        first = compress_demand(demand, 128)
        second = compress_demand(demand, 128)
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 128)
        self.assertAlmostEqual(sum(first.values()), 1.0)

        uniform = compress_demand(uniform_demand(64), 512)
        sources = {source for source, _ in uniform}
        self.assertEqual(len(sources), 64)
        self.assertTrue(all(
            sum(source == item_source for item_source, _ in uniform) == 8
            for source in sources
        ))

    def test_garnet_deterministic_traffic_mapping_guard(self):
        correct = "\n".join(
            f"system.ruby.network.ctrl_traffic_distribution.n{s}.n{63-s} 10"
            for s in range(64)
        )
        stats = traffic_mapping_stats(correct, "bit_complement")
        self.assertEqual(stats["traffic_matrix_mean_active_destinations"], 1)
        self.assertEqual(stats["traffic_expected_destination_fraction"], 1)

        hashed = correct + "\n" + "\n".join(
            f"system.ruby.network.ctrl_traffic_distribution.n{s}.n{s} 10"
            for s in range(64)
        )
        stats = traffic_mapping_stats(hashed, "bit_complement")
        self.assertEqual(stats["traffic_matrix_mean_active_destinations"], 2)
        self.assertEqual(stats["traffic_expected_destination_fraction"], 0.5)

    def test_two_by_two_uniform_metrics(self):
        graph = GridGraph(2)
        metrics = core_metrics(graph, uniform_demand(4))
        self.assertAlmostEqual(metrics["aspl"], 4 / 3)
        self.assertAlmostEqual(metrics["l_max"], 1 / 3)
        self.assertEqual(metrics["diameter"], 2)

    def test_cutstress_is_normalized_and_mirrored(self):
        demand = cutstress_demand(8)
        self.assertAlmostEqual(sum(demand.values()), 1.0)
        self.assertEqual(len(demand), 32)
        for (source, dest), weight in demand.items():
            sx, sy = source % 8, source // 8
            dx, dy = dest % 8, dest // 8
            self.assertEqual((dx, dy), (7 - sx, sy))
            self.assertGreater(weight, 0)

    def test_bidirectional_cutstress_uses_every_source(self):
        demand = cutstress_bidirectional_demand(8)
        self.assertAlmostEqual(sum(demand.values()), 1.0)
        self.assertEqual(len(demand), 64)
        self.assertEqual({source for source, _ in demand}, set(range(64)))
        for source, dest in demand:
            self.assertEqual(dest // 8, source // 8)
            self.assertEqual(dest % 8, 7 - source % 8)

    def test_soc_heterogeneous_demand_is_normalized(self):
        demand = soc_heterogeneous_demand(16)
        self.assertAlmostEqual(sum(demand.values()), 1.0)
        self.assertEqual({source for source, _ in demand}, set(range(256)))
        self.assertTrue(all(source != dest for source, dest in demand))
        destination_share = [
            sum(weight for (source, destination), weight in demand.items()
                if destination == node)
            for node in range(256)
        ]
        # The revised workload remains strongly non-uniform without making a
        # shared endpoint ten times hotter than the uniform mean.
        self.assertAlmostEqual(max(destination_share) * 256, 3.0)

    def test_hotspot_is_normalized(self):
        demand = hotspot_demand(8)
        self.assertAlmostEqual(sum(demand.values()), 1.0)
        self.assertTrue(all(source != dest for source, dest in demand))

    def test_balanced_permutation_traffics(self):
        bit_complement = bit_complement_demand(8)
        tornado = tornado_demand(8)
        for demand in (bit_complement, tornado):
            self.assertAlmostEqual(sum(demand.values()), 1.0)
            self.assertEqual(len(demand), 64)
            self.assertEqual(len({source for source, _ in demand}), 64)
            self.assertEqual(len({dest for _, dest in demand}), 64)
            self.assertTrue(all(source != dest for source, dest in demand))
        self.assertEqual(dict(bit_complement)[(0, 63)], 1 / 64)
        self.assertEqual(dict(tornado)[(0, 3)], 1 / 64)

    def test_greedy_respects_constraints(self):
        edges = greedy_placement(
            n=4,
            demand=uniform_demand(16),
            budget=6,
            max_degree=1,
            d_min=3,
            latency_model="ideal",
            objective="hybrid",
        )
        result = validate_placement(4, edges, budget=6, max_degree=1, d_min=3)
        self.assertLessEqual(result["wire_cost"], 6)
        self.assertLessEqual(result["max_degree"], 1)

    def test_random_placement_is_reproducible(self):
        arguments = dict(
            n=8,
            budget=16,
            max_degree=1,
            d_min=3,
            latency_model="ideal",
            seed=7,
        )
        first = random_placement(**arguments)
        second = random_placement(**arguments)
        self.assertEqual(first, second)
        self.assertEqual(
            validate_placement(8, first, budget=16, max_degree=1, d_min=3)[
                "wire_cost"
            ],
            16,
        )

    def test_stride4_placements_share_the_same_candidate_space(self):
        random_edges = random_placement(
            8, 16, 1, 3, "ideal", seed=7, candidate_mode="stride4"
        )
        greedy_edges = greedy_placement(
            8, uniform_demand(64), 16, 1, 3, "ideal", "aspl",
            candidate_mode="stride4",
        )
        for edges in (random_edges, greedy_edges):
            validate_placement(8, edges, budget=16, max_degree=1, d_min=3)
            for edge in edges:
                ux, uy = edge.u % 8, edge.u // 8
                vx, vy = edge.v % 8, edge.v // 8
                self.assertTrue(ux == vx or uy == vy)
                self.assertEqual(edge.wire_length, 4)

    def test_bounded_candidates_are_loop_free_and_keep_mesh(self):
        edges = random_placement(4, 6, 1, 3, "ideal", seed=7)
        candidates = all_candidates(GridGraph(4, tuple(edges)), k=8)
        self.assertEqual(len(candidates), 16 * 15)
        for routes in candidates.values():
            self.assertLessEqual(len(routes), 8)
            self.assertTrue(any(route.express_count == 0 for route in routes))
            for route in routes:
                self.assertLessEqual(route.express_count, 2)
                self.assertEqual(len(route.routers), len(set(route.routers)))

    def test_candidate_diagnostics_has_three_express_buckets(self):
        diagnostics = candidate_diagnostics(all_candidates(GridGraph(4), k=8))
        self.assertEqual(diagnostics["pair_count"], 16 * 15)
        self.assertEqual(set(diagnostics["pair_fraction_with_express_count"]), {"0", "1", "2"})

    def test_global_q_and_reservation_change_candidate_choice(self):
        graph = GridGraph(4, tuple(random_placement(4, 6, 1, 3, "ideal", seed=7)))
        routes = all_candidates(graph, k=8)[(0, 15)]
        self.assertEqual(choose_candidate(routes).static_latency, min(r.static_latency for r in routes))
        q = {edge_id: 100.0 for route in routes for edge_id in route.express_ids}
        self.assertEqual(choose_candidate(routes, q).express_count, 0)
        reservation_routes, reservations = sequential_reservation_assignment(
            [(routes, ()), (routes, ())]
        )
        self.assertEqual(len(reservation_routes), 2)
        for edge_id in reservation_routes[0].express_ids:
            self.assertGreaterEqual(reservations.get(edge_id, 0), 0)

    def test_committed_route_execution_never_replans(self):
        graph = GridGraph(4, tuple(random_placement(4, 6, 1, 3, "ideal", seed=7)))
        route = next(r for r in all_candidates(graph, k=8)[(0, 15)] if r.express_count)
        state = RouteExecutionState(route)
        traversals = []
        while not state.completed:
            _, is_express = state.advance()
            traversals.append(is_express)
        self.assertEqual(sum(traversals), route.express_count)
        self.assertEqual(state.current_router, 15)

    def test_candidate_bundle_is_json_safe_and_preserves_route_state(self):
        graph = GridGraph(4, tuple(random_placement(4, 6, 1, 3, "ideal", seed=7)))
        routes = all_candidates(graph, k=8)
        bundle = candidate_bundle(routes)
        self.assertEqual(bundle["diagnostics"]["pair_count"], 16 * 15)
        item = next(item for item in bundle["routes"] if item["source"] == 0 and item["destination"] == 15)
        self.assertTrue(all("express_ids" in route and "static_latency" in route for route in item["candidates"]))


if __name__ == "__main__":
    unittest.main()
