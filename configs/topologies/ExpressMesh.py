"""A 2D mesh augmented with budgeted bidirectional express links."""

import json
from itertools import permutations
from pathlib import Path

from m5.objects import *

from common import FileSystemConfig
from topologies.BaseTopology import SimpleTopology


class ExpressMesh(SimpleTopology):
    description = "ExpressMesh"

    def __init__(self, controllers):
        self.nodes = controllers

    @staticmethod
    def _load_express_links(options, num_routers, num_rows):
        if not options.express_links_file:
            raise ValueError("ExpressMesh requires --express-links-file")
        path = Path(options.express_links_file)
        with path.open(encoding="utf-8") as topology_file:
            topology = json.load(topology_file)

        if topology.get("dimension") != num_rows:
            raise ValueError(
                f"topology dimension {topology.get('dimension')} does not "
                f"match --mesh-rows={num_rows}"
            )
        if topology.get("node_count") != num_routers:
            raise ValueError(
                f"topology node count {topology.get('node_count')} does not "
                f"match --num-cpus={num_routers}"
            )

        seen = set()
        degree = [0] * num_routers
        links = []
        for record in topology.get("express_links", []):
            u = int(record["u"])
            v = int(record["v"])
            latency = int(record["latency"])
            wire_length = int(record["wire_length"])
            if not 0 <= u < num_routers or not 0 <= v < num_routers or u == v:
                raise ValueError(f"invalid express endpoints: {(u, v)}")
            key = tuple(sorted((u, v)))
            if key in seen:
                raise ValueError(f"duplicate express link: {key}")
            ux, uy = u % num_rows, u // num_rows
            vx, vy = v % num_rows, v // num_rows
            actual_wire_length = abs(ux - vx) + abs(uy - vy)
            if wire_length != actual_wire_length or wire_length <= 1:
                raise ValueError(f"invalid express wire length for {key}")
            if latency < 1:
                raise ValueError(f"invalid express latency for {key}")
            seen.add(key)
            degree[u] += 1
            degree[v] += 1
            links.append((u, v, latency))

        constraints = topology.get("constraints", {})
        actual_wire_cost = sum(record["wire_length"] for record in topology.get(
            "express_links", []))
        declared_wire_cost = constraints.get("wire_cost")
        if declared_wire_cost is None or declared_wire_cost != actual_wire_cost:
            raise ValueError(
                "JSON wire_cost does not match express links: "
                f"declared={declared_wire_cost}, actual={actual_wire_cost}"
            )
        if actual_wire_cost > options.express_budget:
            raise ValueError(
                f"express wire budget exceeded: {actual_wire_cost} > "
                f"{options.express_budget}"
            )
        actual_max_degree = max(degree, default=0)
        declared_max_degree = constraints.get("max_degree")
        if declared_max_degree is None or actual_max_degree != declared_max_degree:
            raise ValueError("JSON max_degree does not match express links")
        if actual_max_degree > options.express_max_degree:
            raise ValueError(
                f"express max degree exceeded: {actual_max_degree} > "
                f"{options.express_max_degree}"
            )
        min_wire_length = options.express_min_wire_length
        if any(record["wire_length"] < min_wire_length
               for record in topology.get("express_links", [])):
            raise ValueError(
                f"express link is shorter than d_min={min_wire_length}"
            )
        return links

    @staticmethod
    def _xy_segment(source, destination, num_rows):
        x, y = source % num_rows, source // num_rows
        dx, dy = destination % num_rows, destination // num_rows
        path = [source]
        while x != dx:
            x += 1 if dx > x else -1
            path.append(y * num_rows + x)
        while y != dy:
            y += 1 if dy > y else -1
            path.append(y * num_rows + x)
        return path

    @classmethod
    def _source_route_table(cls, links, num_routers, num_rows):
        """Choose the lowest-static-cost complete route for every pair.

        This is the injection MVP. The offline implementation retains K=8
        candidates; gem5 initially consumes the deterministic q=r=0 winner.
        """
        directed = []
        for express_id, (u, v, latency) in enumerate(links):
            directed.append((express_id * 2, u, v, latency))
            directed.append((express_id * 2 + 1, v, u, latency))

        counts = [0] * (num_routers * num_routers)
        ids = [0] * (num_routers * num_routers * 2)
        candidate_counts = [0] * (num_routers * num_routers)
        candidate_latencies = [0] * (num_routers * num_routers * 8)
        candidate_express_counts = [0] * (num_routers * num_routers * 8)
        candidate_express_ids = [0] * (num_routers * num_routers * 8 * 2)
        for source in range(num_routers):
            for destination in range(num_routers):
                if source == destination:
                    continue
                candidates = []
                for count in range(3):
                    for sequence in permutations(directed, count):
                        first_target = sequence[0][1] if sequence else destination
                        routers = cls._xy_segment(source, first_target, num_rows)
                        latency = len(routers) - 1
                        valid = True
                        express_ids = []
                        for index, (directed_id, entry, exit_node, edge_latency) in enumerate(sequence):
                            if routers[-1] != entry:
                                valid = False
                                break
                            routers.append(exit_node)
                            latency += edge_latency
                            express_ids.append(directed_id)
                            target = destination if index + 1 == len(sequence) else sequence[index + 1][1]
                            segment = cls._xy_segment(exit_node, target, num_rows)
                            routers.extend(segment[1:])
                            latency += len(segment) - 1
                        if valid and routers[-1] == destination and len(routers) == len(set(routers)):
                            candidates.append((latency, count, tuple(express_ids)))
                candidates.sort(key=lambda item: (item[0], item[1], item[2]))
                if not candidates:
                    raise ValueError(f"no source route for pair {(source, destination)}")
                selected = candidates[:8]
                pair_index = source * num_routers + destination
                candidate_counts[pair_index] = len(selected)
                for c, (latency, ec, eids) in enumerate(selected):
                    base = pair_index * 8 + c
                    candidate_latencies[base] = latency
                    candidate_express_counts[base] = ec
                    for i, eid in enumerate(eids):
                        candidate_express_ids[base * 2 + i] = eid
                _, count, express_ids = selected[0]
                counts[pair_index] = count
                for index, directed_id in enumerate(express_ids):
                    ids[pair_index * 2 + index] = directed_id
        return (counts, ids, candidate_counts, candidate_latencies,
                candidate_express_counts, candidate_express_ids)

    def makeTopology(self, options, network, IntLink, ExtLink, Router):
        nodes = self.nodes
        num_routers = options.num_cpus
        num_rows = options.mesh_rows
        link_latency = options.link_latency
        router_latency = options.router_latency

        if num_rows <= 0 or num_routers % num_rows != 0:
            raise ValueError("ExpressMesh requires a rectangular mesh")
        num_columns = num_routers // num_rows
        if num_columns != num_rows:
            raise ValueError("phase-two ExpressMesh requires a square mesh")
        express_links = self._load_express_links(
            options, num_routers, num_rows
        )

        routers = [
            Router(router_id=router_id, latency=router_latency)
            for router_id in range(num_routers)
        ]
        network.routers = routers
        network.express_mesh_link_latency = link_latency
        network.express_link_endpoints = [
            endpoint for u, v, _ in express_links for endpoint in (u, v)
        ]
        network.express_link_latencies = [
            latency for _, _, latency in express_links
        ]
        (counts, route_ids, candidate_counts, candidate_latencies,
         candidate_express_counts, candidate_express_ids) = self._source_route_table(
            express_links, num_routers, num_rows
        )
        network.source_route_express_counts = counts
        network.source_route_express_ids = route_ids
        network.source_route_candidate_counts = candidate_counts
        network.source_route_candidate_latencies = candidate_latencies
        network.source_route_candidate_express_counts = candidate_express_counts
        network.source_route_candidate_express_ids = candidate_express_ids

        cntrls_per_router, remainder = divmod(len(nodes), num_routers)
        network_nodes = nodes[: len(nodes) - remainder]
        remainder_nodes = nodes[len(nodes) - remainder :]
        link_count = 0
        ext_links = []
        for index, node in enumerate(network_nodes):
            cntrl_level, router_id = divmod(index, num_routers)
            assert cntrl_level < cntrls_per_router
            ext_links.append(
                ExtLink(
                    link_id=link_count,
                    ext_node=node,
                    int_node=routers[router_id],
                    latency=link_latency,
                )
            )
            link_count += 1
        for index, node in enumerate(remainder_nodes):
            assert node.type == "DMA_Controller"
            assert index < remainder
            ext_links.append(
                ExtLink(
                    link_id=link_count,
                    ext_node=node,
                    int_node=routers[0],
                    latency=link_latency,
                )
            )
            link_count += 1
        network.ext_links = ext_links

        int_links = []

        def add_link(source, dest, outport, inport, latency):
            nonlocal link_count
            int_links.append(
                IntLink(
                    link_id=link_count,
                    src_node=routers[source],
                    dst_node=routers[dest],
                    src_outport=outport,
                    dst_inport=inport,
                    latency=latency,
                    weight=1,
                )
            )
            link_count += 1

        for row in range(num_rows):
            for col in range(num_columns - 1):
                west = col + row * num_columns
                east = west + 1
                add_link(west, east, "East", "West", link_latency)
                add_link(east, west, "West", "East", link_latency)

        for col in range(num_columns):
            for row in range(num_rows - 1):
                south = col + row * num_columns
                north = south + num_columns
                add_link(south, north, "North", "South", link_latency)
                add_link(north, south, "South", "North", link_latency)

        for u, v, latency in express_links:
            add_link(u, v, f"ExpressTo{v}", f"ExpressFrom{u}", latency)
            add_link(v, u, f"ExpressTo{u}", f"ExpressFrom{v}", latency)

        network.int_links = int_links

    def registerTopology(self, options):
        for router_id in range(options.num_cpus):
            FileSystemConfig.register_node(
                [router_id],
                MemorySize(options.mem_size) // options.num_cpus,
                router_id,
            )
