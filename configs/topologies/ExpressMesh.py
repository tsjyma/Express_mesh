"""A 2D mesh augmented with budgeted bidirectional express links."""

import hashlib
import json
import heapq
import os
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
    def _source_route_table(
        cls, links, num_routers, num_rows, candidate_limit,
        retain_mesh_candidate=False,
    ):
        """Retain the exact K lowest-cost loop-free routes for every pair.

        A route contains zero, one, or two directed express links and uses XY
        mesh segments between them.  The original implementation enumerated
        every two-link permutation separately for every source/destination
        pair.  This lazy merge preserves the same ordering while making
        16x16 configurations practical.
        """
        if candidate_limit <= 0:
            raise ValueError("source-route candidate count K must be positive")
        directed = []
        for express_id, (u, v, latency) in enumerate(links):
            directed.append((express_id * 2, u, v, latency))
            directed.append((express_id * 2 + 1, v, u, latency))

        def distance(first, second):
            fx, fy = first % num_rows, first // num_rows
            sx, sy = second % num_rows, second // num_rows
            return abs(fx - sx) + abs(fy - sy)

        def compose(source, destination, sequence):
            first_target = sequence[0][1] if sequence else destination
            routers = cls._xy_segment(source, first_target, num_rows)
            latency = len(routers) - 1
            express_ids = []
            for index, (directed_id, entry, exit_node,
                        edge_latency) in enumerate(sequence):
                if routers[-1] != entry:
                    return None
                routers.append(exit_node)
                latency += edge_latency
                express_ids.append(directed_id)
                target = (destination if index + 1 == len(sequence)
                          else sequence[index + 1][1])
                segment = cls._xy_segment(exit_node, target, num_rows)
                routers.extend(segment[1:])
                latency += len(segment) - 1
            if routers[-1] != destination or len(routers) != len(set(routers)):
                return None
            return latency, len(sequence), tuple(express_ids)

        counts = [0] * (num_routers * num_routers)
        ids = [0] * (num_routers * num_routers * 2)
        candidate_counts = [0] * (num_routers * num_routers)
        candidate_latencies = [0] * (
            num_routers * num_routers * candidate_limit
        )
        candidate_express_counts = [0] * (
            num_routers * num_routers * candidate_limit
        )
        candidate_express_ids = [0] * (
            num_routers * num_routers * candidate_limit * 2
        )
        for destination in range(num_routers):
            # For a fixed destination and first express edge, sort all legal
            # second-edge tails once.  Adding the source-to-first-entry cost
            # is a constant, so each list remains sorted for every source.
            second_edges = []
            for first_index, first in enumerate(directed):
                choices = []
                for second_index, second in enumerate(directed):
                    if second_index == first_index:
                        continue
                    tail_latency = (
                        distance(first[2], second[1]) + second[3] +
                        distance(second[2], destination)
                    )
                    choices.append((tail_latency, second[0], second_index))
                choices.sort()
                second_edges.append(choices)

            for source in range(num_routers):
                if source == destination:
                    continue
                queue = []
                mesh_latency = distance(source, destination)
                heapq.heappush(queue, (mesh_latency, 0, (), -1, -1))
                for edge_index, edge in enumerate(directed):
                    one_latency = (distance(source, edge[1]) + edge[3] +
                                   distance(edge[2], destination))
                    heapq.heappush(
                        queue, (one_latency, 1, (edge[0],), -1, edge_index)
                    )
                    if second_edges[edge_index]:
                        tail_latency, second_id, _ = second_edges[edge_index][0]
                        two_latency = (distance(source, edge[1]) + edge[3] +
                                       tail_latency)
                        heapq.heappush(
                            queue,
                            (two_latency, 2, (edge[0], second_id),
                             edge_index, 0),
                        )

                selected = []
                while queue and len(selected) < candidate_limit:
                    _, count, _, first_index, choice_index = heapq.heappop(queue)
                    if count == 0:
                        sequence = ()
                    elif count == 1:
                        sequence = (directed[choice_index],)
                    else:
                        choices = second_edges[first_index]
                        _, _, second_index = choices[choice_index]
                        sequence = (directed[first_index], directed[second_index])
                        next_choice = choice_index + 1
                        if next_choice < len(choices):
                            tail_latency, second_id, _ = choices[next_choice]
                            first = directed[first_index]
                            next_latency = (
                                distance(source, first[1]) + first[3] +
                                tail_latency
                            )
                            heapq.heappush(
                                queue,
                                (next_latency, 2, (first[0], second_id),
                                 first_index, next_choice),
                            )
                    candidate = compose(source, destination, sequence)
                    if candidate is not None:
                        selected.append(candidate)

                if (retain_mesh_candidate and
                        not any(express_count == 0
                                for _, express_count, _ in selected)):
                    mesh = (distance(source, destination), 0, ())
                    if len(selected) >= candidate_limit:
                        selected[-1] = mesh
                    else:
                        selected.append(mesh)
                    selected.sort()

                if not selected:
                    raise ValueError(
                        f"no source route for pair {(source, destination)}"
                    )
                pair_index = source * num_routers + destination
                candidate_counts[pair_index] = len(selected)
                for c, (latency, ec, eids) in enumerate(selected):
                    base = pair_index * candidate_limit + c
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

    @classmethod
    def _cached_source_route_table(
        cls, options, links, num_routers, num_rows, candidate_limit,
        retain_mesh_candidate,
    ):
        cache_dir = getattr(options, "express_route_cache_dir", "")
        if not cache_dir:
            return cls._source_route_table(
                links, num_routers, num_rows, candidate_limit,
                retain_mesh_candidate,
            )
        payload = {
            "version": 1,
            "links": links,
            "num_routers": num_routers,
            "num_rows": num_rows,
            "candidate_limit": candidate_limit,
            "retain_mesh_candidate": retain_mesh_candidate,
        }
        key = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        directory = Path(cache_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (key + ".json")
        if path.exists():
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("key") == key:
                return tuple(record["arrays"])
        arrays = cls._source_route_table(
            links, num_routers, num_rows, candidate_limit,
            retain_mesh_candidate,
        )
        temporary = directory / f".{key}.{os.getpid()}.tmp"
        temporary.write_text(json.dumps({"key": key, "arrays": arrays}),
                             encoding="utf-8")
        os.replace(temporary, path)
        return arrays

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
        network.express_link_endpoints = [
            endpoint for u, v, _ in express_links for endpoint in (u, v)
        ]
        network.express_link_latencies = [
            latency for _, _, latency in express_links
        ]
        (counts, route_ids, candidate_counts, candidate_latencies,
         candidate_express_counts, candidate_express_ids) = self._cached_source_route_table(
            options, express_links, num_routers, num_rows,
            options.express_source_route_candidates,
            options.express_retain_mesh_candidate,
        )
        network.source_route_candidates = options.express_source_route_candidates
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
