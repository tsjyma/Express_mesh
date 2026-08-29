/*
 * Copyright (c) 2008 Princeton University
 * Copyright (c) 2016 Georgia Institute of Technology
 * All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met: redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer;
 * redistributions in binary form must reproduce the above copyright
 * notice, this list of conditions and the following disclaimer in the
 * documentation and/or other materials provided with the distribution;
 * neither the name of the copyright holders nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */


#include "mem/ruby/network/garnet/RoutingUnit.hh"

#include <limits>

#include "base/cast.hh"
#include "base/compiler.hh"
#include "base/logging.hh"
#include "base/random.hh"
#include "debug/RubyNetwork.hh"
#include "mem/ruby/network/garnet/InputUnit.hh"
#include "mem/ruby/network/garnet/OutputUnit.hh"
#include "mem/ruby/network/garnet/Router.hh"
#include "mem/ruby/slicc_interface/Message.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

RoutingUnit::RoutingUnit(Router *router)
{
    m_router = router;
    m_routing_table.clear();
    m_weight_table.clear();
}

void
RoutingUnit::addRoute(std::vector<NetDest>& routing_table_entry)
{
    if (routing_table_entry.size() > m_routing_table.size()) {
        m_routing_table.resize(routing_table_entry.size());
    }
    for (int v = 0; v < routing_table_entry.size(); v++) {
        m_routing_table[v].push_back(routing_table_entry[v]);
    }
}

void
RoutingUnit::addWeight(int link_weight)
{
    m_weight_table.push_back(link_weight);
}

bool
RoutingUnit::supportsVnet(int vnet, std::vector<int> sVnets)
{
    // If all vnets are supported, return true
    if (sVnets.size() == 0) {
        return true;
    }

    // Find the vnet in the vector, return true
    if (std::find(sVnets.begin(), sVnets.end(), vnet) != sVnets.end()) {
        return true;
    }

    // Not supported vnet
    return false;
}

/*
 * This is the default routing algorithm in garnet.
 * The routing table is populated during topology creation.
 * Routes can be biased via weight assignments in the topology file.
 * Correct weight assignments are critical to provide deadlock avoidance.
 */
int
RoutingUnit::lookupRoutingTable(int vnet, NetDest msg_destination)
{
    // First find all possible output link candidates
    // For ordered vnet, just choose the first
    // (to make sure different packets don't choose different routes)
    // For unordered vnet, randomly choose any of the links
    // To have a strict ordering between links, they should be given
    // different weights in the topology file

    int output_link = -1;
    int min_weight = INFINITE_;
    std::vector<int> output_link_candidates;
    int num_candidates = 0;

    // Identify the minimum weight among the candidate output links
    for (int link = 0; link < m_routing_table[vnet].size(); link++) {
        if (msg_destination.intersectionIsNotEmpty(
            m_routing_table[vnet][link])) {

        if (m_weight_table[link] <= min_weight)
            min_weight = m_weight_table[link];
        }
    }

    // Collect all candidate output links with this minimum weight
    for (int link = 0; link < m_routing_table[vnet].size(); link++) {
        if (msg_destination.intersectionIsNotEmpty(
            m_routing_table[vnet][link])) {

            if (m_weight_table[link] == min_weight) {
                num_candidates++;
                output_link_candidates.push_back(link);
            }
        }
    }

    if (output_link_candidates.size() == 0) {
        fatal("Fatal Error:: No Route exists from this Router.");
        exit(0);
    }

    // Randomly select any candidate output link
    int candidate = 0;
    if (!(m_router->get_net_ptr())->isVNetOrdered(vnet))
        candidate = random_mt.random<int>(0, num_candidates - 1);

    output_link = output_link_candidates.at(candidate);
    return output_link;
}


void
RoutingUnit::addInDirection(PortDirection inport_dirn, int inport_idx)
{
    m_inports_dirn2idx[inport_dirn] = inport_idx;
    m_inports_idx2dirn[inport_idx]  = inport_dirn;
}

void
RoutingUnit::addOutDirection(PortDirection outport_dirn, int outport_idx)
{
    m_outports_dirn2idx[outport_dirn] = outport_idx;
    m_outports_idx2dirn[outport_idx]  = outport_dirn;
}

// outportCompute() is called by the InputUnit
// It calls the routing table by default.
// A template for adaptive topology-specific routing algorithm
// implementations using port directions rather than a static routing
// table is provided here.

int
RoutingUnit::outportCompute(RouteInfo route, int inport,
                            PortDirection inport_dirn)
{
    int outport = -1;

    if (route.dest_router == m_router->get_id()) {

        // Multiple NIs may be connected to this router,
        // all with output port direction = "Local"
        // Get exact outport id from table
        outport = lookupRoutingTable(route.vnet, route.net_dest);
        return outport;
    }

    // Routing Algorithm set in GarnetNetwork.py
    // Can be over-ridden from command line using --routing-algorithm = 1
    RoutingAlgorithm routing_algorithm =
        (RoutingAlgorithm) m_router->get_net_ptr()->getRoutingAlgorithm();

    switch (routing_algorithm) {
        case TABLE_:  outport =
            lookupRoutingTable(route.vnet, route.net_dest); break;
        case XY_:     outport =
            outportComputeXY(route, inport, inport_dirn); break;
        // any custom algorithm
        case CUSTOM_: outport =
            outportComputeCustom(route, inport, inport_dirn); break;
        default: outport =
            lookupRoutingTable(route.vnet, route.net_dest); break;
    }

    assert(outport != -1);
    return outport;
}

// XY routing implemented using port directions
// Only for reference purpose in a Mesh
// By default Garnet uses the routing table
int
RoutingUnit::outportComputeXY(RouteInfo route,
                              int inport,
                              PortDirection inport_dirn)
{
    PortDirection outport_dirn = "Unknown";

    [[maybe_unused]] int num_rows = m_router->get_net_ptr()->getNumRows();
    int num_cols = m_router->get_net_ptr()->getNumCols();
    assert(num_rows > 0 && num_cols > 0);

    int my_id = m_router->get_id();
    int my_x = my_id % num_cols;
    int my_y = my_id / num_cols;

    int dest_id = route.dest_router;
    int dest_x = dest_id % num_cols;
    int dest_y = dest_id / num_cols;

    int x_hops = abs(dest_x - my_x);
    int y_hops = abs(dest_y - my_y);

    bool x_dirn = (dest_x >= my_x);
    bool y_dirn = (dest_y >= my_y);

    // already checked that in outportCompute() function
    assert(!(x_hops == 0 && y_hops == 0));

    if (x_hops > 0) {
        if (x_dirn) {
            assert(inport_dirn == "Local" || inport_dirn == "West");
            outport_dirn = "East";
        } else {
            assert(inport_dirn == "Local" || inport_dirn == "East");
            outport_dirn = "West";
        }
    } else if (y_hops > 0) {
        if (y_dirn) {
            // "Local" or "South" or "West" or "East"
            assert(inport_dirn != "North");
            outport_dirn = "North";
        } else {
            // "Local" or "North" or "West" or "East"
            assert(inport_dirn != "South");
            outport_dirn = "South";
        }
    } else {
        // x_hops == 0 and y_hops == 0
        // this is not possible
        // already checked that in outportCompute() function
        panic("x_hops == y_hops == 0");
    }

    return m_outports_dirn2idx[outport_dirn];
}

// Template for implementing custom routing algorithm
// using port directions. (Example adaptive)
int
RoutingUnit::outportComputeCustom(RouteInfo route,
                                 int inport,
                                 PortDirection inport_dirn)
{
    if (route.source_routed)
        return outportComputeExpressMesh(route);
    return outportComputeExpressMesh(route);
}

int
RoutingUnit::outportComputeLocalAdaptive(RouteInfo route, int waypoint,
                                          int inport, PortDirection inport_dirn)
{
    const int current = m_router->get_id();
    const int cols = m_router->get_net_ptr()->getNumCols();
    const int cx = current % cols, cy = current / cols;
    const int wx = waypoint % cols, wy = waypoint / cols;
    const int dx = wx - cx, dy = wy - cy;
    if (dx == 0 && dy == 0)
        return -1;
    std::vector<int> outputs;
    auto consider = [&](const char *name, int nx, int ny, bool require_free) {
        if (nx < 0 || ny < 0 || nx >= cols || ny >= m_router->get_net_ptr()->getNumRows())
            return;
        if (std::abs(wx - nx) + std::abs(wy - ny) !=
            std::abs(dx) + std::abs(dy) - 1)
            return;
        auto it = m_outports_dirn2idx.find(PortDirection(name));
        if (it == m_outports_dirn2idx.end()) return;
        if (!require_free ||
            m_router->getOutputUnit(it->second)->has_free_vc(route.vnet, false))
            outputs.push_back(it->second);
    };
    if (dx > 0) consider("East", cx + 1, cy, true);
    if (dx < 0) consider("West", cx - 1, cy, true);
    if (dy > 0) consider("North", cx, cy + 1, true);
    if (dy < 0) consider("South", cx, cy - 1, true);
    if (outputs.empty()) {
        if (dx > 0) consider("East", cx + 1, cy, false);
        if (dx < 0) consider("West", cx - 1, cy, false);
        if (dy > 0) consider("North", cx, cy + 1, false);
        if (dy < 0) consider("South", cx, cy - 1, false);
    }
    fatal_if(outputs.empty(), "no productive mesh output from %d to %d", current, waypoint);
    int best = outputs.front();
    double best_congestion = m_router->getOutputUnit(best)->congestion(route.vnet);
    std::vector<int> ties{best};
    for (size_t i = 1; i < outputs.size(); ++i) {
        const int out = outputs[i];
        const double c = m_router->getOutputUnit(out)->congestion(route.vnet);
        if (c < best_congestion) { best_congestion = c; ties = {out}; }
        else if (c == best_congestion) ties.push_back(out);
    }
    return ties[random_mt.random<int>(0, ties.size() - 1)];
}

int
RoutingUnit::outportComputeExpressMesh(RouteInfo route)
{
    GarnetNetwork *network = m_router->get_net_ptr();
    const int num_cols = network->getNumCols();
    const int current = m_router->get_id();

    // Coordinate-only X-then-Y routing to a mesh waypoint.  Keeping this
    // separate from Garnet's input-direction assertions is intentional:
    // source-route segments may start immediately after an express link.
    auto meshXYTo = [&](int waypoint) {
        const int cx=current%num_cols,cy=current/num_cols;
        const int wx=waypoint%num_cols,wy=waypoint/num_cols;
        PortDirection direction;
        if(cx<wx)direction="East";
        else if(cx>wx)direction="West";
        else if(cy<wy)direction="North";
        else if(cy>wy)direction="South";
        else fatal("mesh XY waypoint equals current router %d", current);
        const auto outport=m_outports_dirn2idx.find(direction);
        fatal_if(outport==m_outports_dirn2idx.end(),
                 "Router %d has no mesh XY output %s toward %d",
                 current, direction, waypoint);
        return outport->second;
    };

    // Escape never uses an express link and never transitions back.  Its
    // channel-dependency graph is therefore the ordinary acyclic mesh-XY CDG.
    if (route.escape_vc)
        return meshXYTo(route.dest_router);

    auto directionFor = [current, num_cols](int neighbor) {
        if (neighbor == current + 1 &&
            current / num_cols == neighbor / num_cols)
            return PortDirection("East");
        if (neighbor == current - 1 &&
            current / num_cols == neighbor / num_cols)
            return PortDirection("West");
        if (neighbor == current + num_cols)
            return PortDirection("North");
        if (neighbor == current - num_cols)
            return PortDirection("South");
        return PortDirection("ExpressTo" + std::to_string(neighbor));
    };

    if (route.source_routed) {
        fatal_if(route.express_stage > route.express_count,
                 "invalid source-route stage %d/%d", route.express_stage,
                 route.express_count);
        if (route.express_stage < route.express_count) {
            const int directed_id = route.express_ids.at(route.express_stage);
            const int entry = network->getExpressRouteSource(directed_id);
            const int exit = network->getExpressRouteDestination(directed_id);
            if (current != entry)
                return meshXYTo(entry);
            const auto outport = m_outports_dirn2idx.find(directionFor(exit));
            fatal_if(outport == m_outports_dirn2idx.end(),
                     "Router %d has no source-route express output to %d",
                     current, exit);
            return outport->second;
        }
        return meshXYTo(route.dest_router);
    }

    const int minimal_next =
        network->getExpressNextHop(current, route.dest_router);
    int next = minimal_next;

    if (network->isExpressAdaptive()) {
        const uint32_t minimal_distance =
            network->getExpressDistance(current, route.dest_router);
        const int minimal_outport =
            m_outports_dirn2idx.at(directionFor(minimal_next));
        const double minimal_congestion =
            m_router->getOutputUnit(minimal_outport)->congestion(route.vnet);

        if (minimal_congestion >= network->getExpressAdaptiveThreshold()) {
            double best_score = std::numeric_limits<double>::infinity();
            std::vector<int> best_neighbors;
            for (const int neighbor : network->getExpressNeighbors(current)) {
                const uint32_t remaining =
                    network->getExpressDistance(neighbor, route.dest_router);
                if (remaining >= minimal_distance)
                    continue;
                const uint32_t path_length =
                    network->getExpressEdgeLatency(current, neighbor) +
                    remaining;
                if (double(path_length) >
                    network->getExpressDetourRatio() * minimal_distance)
                    continue;
                const int outport =
                    m_outports_dirn2idx.at(directionFor(neighbor));
                const double congestion =
                    m_router->getOutputUnit(outport)->congestion(route.vnet);
                const double score = path_length *
                    (1.0 + network->getExpressAdaptiveLambda() * congestion);
                if (score < best_score) {
                    best_score = score;
                    best_neighbors.assign(1, neighbor);
                } else if (score == best_score) {
                    best_neighbors.push_back(neighbor);
                }
            }
            if (!best_neighbors.empty())
                next = best_neighbors[random_mt.random<int>(
                    0, best_neighbors.size() - 1)];
        }
        const uint32_t selected_length =
            network->getExpressEdgeLatency(current, next) +
            network->getExpressDistance(next, route.dest_router);
        if (selected_length > minimal_distance)
            network->incrementNonminimalDecision();
    }

    const PortDirection direction = directionFor(next);
    const auto outport = m_outports_dirn2idx.find(direction);
    fatal_if(outport == m_outports_dirn2idx.end(),
             "Router %d has no output port %s for next hop %d",
             current, direction.c_str(), next);
    return outport->second;
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
