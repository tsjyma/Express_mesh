/*
 * Copyright (c) 2020 Advanced Micro Devices, Inc.
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


#include "mem/ruby/network/garnet/GarnetNetwork.hh"

#include <algorithm>
#include <functional>
#include <limits>
#include <queue>
#include <utility>

#include <cassert>

#include "base/cast.hh"
#include "base/compiler.hh"
#include "base/random.hh"
#include "debug/RubyNetwork.hh"
#include "mem/ruby/common/NetDest.hh"
#include "mem/ruby/network/MessageBuffer.hh"
#include "mem/ruby/network/garnet/CommonTypes.hh"
#include "mem/ruby/network/garnet/CreditLink.hh"
#include "mem/ruby/network/garnet/GarnetLink.hh"
#include "mem/ruby/network/garnet/NetworkInterface.hh"
#include "mem/ruby/network/garnet/NetworkLink.hh"
#include "mem/ruby/network/garnet/Router.hh"
#include "mem/ruby/system/RubySystem.hh"

namespace gem5
{

namespace ruby
{

namespace garnet
{

/*
 * GarnetNetwork sets up the routers and links and collects stats.
 * Default parameters (GarnetNetwork.py) can be overwritten from command line
 * (see configs/network/Network.py)
 */

GarnetNetwork::GarnetNetwork(const Params &p)
    : Network(p)
{
    m_num_rows = p.num_rows;
    m_ni_flit_size = p.ni_flit_size;
    m_max_vcs_per_vnet = 0;
    m_buffers_per_data_vc = p.buffers_per_data_vc;
    m_buffers_per_ctrl_vc = p.buffers_per_ctrl_vc;
    m_routing_algorithm = p.routing_algorithm;
    m_express_mesh_link_latency = p.express_mesh_link_latency;
    m_express_link_endpoints = p.express_link_endpoints;
    m_express_link_latencies = p.express_link_latencies;
    m_source_route_enabled = p.source_route_enabled;
    m_source_route_express_counts = p.source_route_express_counts;
    m_source_route_express_ids = p.source_route_express_ids;
    m_source_route_candidate_counts = p.source_route_candidate_counts;
    m_source_route_candidate_latencies = p.source_route_candidate_latencies;
    m_source_route_candidate_express_counts = p.source_route_candidate_express_counts;
    m_source_route_candidate_express_ids = p.source_route_candidate_express_ids;
    m_source_route_policy = p.source_route_policy;
    m_reservation_current.assign(2 * m_express_link_latencies.size(), 0);
    m_express_adaptive = p.express_adaptive;
    m_express_adaptive_threshold = p.express_adaptive_threshold;
    m_express_adaptive_lambda = p.express_adaptive_lambda;
    m_express_detour_ratio = p.express_detour_ratio;
    m_express_escape_timeout = p.express_escape_timeout;
    m_express_escape_enabled = p.express_escape_enabled;
    m_next_packet_id = 0;

    fatal_if(m_source_route_enabled && m_routing_algorithm != CUSTOM_,
             "source-route injection requires custom routing");

    m_enable_fault_model = p.enable_fault_model;
    if (m_enable_fault_model)
        fault_model = p.fault_model;

    m_vnet_type.resize(m_virtual_networks);

    for (int i = 0 ; i < m_virtual_networks ; i++) {
        if (m_vnet_type_names[i] == "response")
            m_vnet_type[i] = DATA_VNET_; // carries data (and ctrl) packets
        else
            m_vnet_type[i] = CTRL_VNET_; // carries only ctrl packets
    }

    // record the routers
    for (std::vector<BasicRouter*>::const_iterator i =  p.routers.begin();
         i != p.routers.end(); ++i) {
        Router* router = safe_cast<Router*>(*i);
        m_routers.push_back(router);

        // initialize the router's network pointers
        router->init_net_ptr(this);
    }

    // record the network interfaces
    for (std::vector<ClockedObject*>::const_iterator i = p.netifs.begin();
         i != p.netifs.end(); ++i) {
        NetworkInterface *ni = safe_cast<NetworkInterface *>(*i);
        m_nis.push_back(ni);
        ni->init_net_ptr(this);
    }

    // Print Garnet version
    inform("Garnet version %s\n", garnetVersion);
}

void
GarnetNetwork::init()
{
    Network::init();

    for (int i=0; i < m_nodes; i++) {
        m_nis[i]->addNode(m_toNetQueues[i], m_fromNetQueues[i]);
    }

    // The topology pointer should have already been initialized in the
    // parent network constructor
    assert(m_topology_ptr != NULL);
    m_topology_ptr->createLinks(this);
    m_reservation_current.assign(2 * m_express_link_latencies.size(), 0);

    // Initialize topology specific parameters
    if (getNumRows() > 0) {
        // Only for Mesh topology
        // m_num_rows and m_num_cols are only used for
        // implementing XY or custom routing in RoutingUnit.cc
        m_num_rows = getNumRows();
        m_num_cols = m_routers.size() / m_num_rows;
        assert(m_num_rows * m_num_cols == m_routers.size());

        const int router_count = m_routers.size();
        fatal_if(m_express_link_endpoints.size() !=
                     2 * m_express_link_latencies.size(),
                 "Express-link endpoint and latency vectors disagree");
        std::vector<std::vector<std::pair<int, uint32_t>>> adjacency(
            router_count);
        auto add_edge = [&adjacency](int u, int v, uint32_t latency) {
            adjacency[u].emplace_back(v, latency);
            adjacency[v].emplace_back(u, latency);
        };
        for (int row = 0; row < m_num_rows; ++row) {
            for (int col = 0; col < m_num_cols; ++col) {
                const int router = row * m_num_cols + col;
                if (col + 1 < m_num_cols) {
                    add_edge(router, router + 1,
                             m_express_mesh_link_latency);
                }
                if (row + 1 < m_num_rows) {
                    add_edge(router, router + m_num_cols,
                             m_express_mesh_link_latency);
                }
            }
        }
        for (int index = 0; index < m_express_link_latencies.size(); ++index) {
            const int u = m_express_link_endpoints[2 * index];
            const int v = m_express_link_endpoints[2 * index + 1];
            fatal_if(u < 0 || u >= router_count || v < 0 || v >= router_count,
                     "Express-link endpoint outside router range");
            add_edge(u, v, m_express_link_latencies[index]);
        }

        const uint32_t infinity = std::numeric_limits<uint32_t>::max();
        m_express_neighbors.assign(router_count, {});
        m_express_edge_latency.assign(
            router_count, std::vector<uint32_t>(router_count, infinity));
        for (int router = 0; router < router_count; ++router) {
            for (const auto &[neighbor, latency] : adjacency[router]) {
                m_express_neighbors[router].push_back(neighbor);
                m_express_edge_latency[router][neighbor] = latency;
            }
            std::sort(m_express_neighbors[router].begin(),
                      m_express_neighbors[router].end());
        }
        m_express_next_hop.assign(
            router_count, std::vector<int>(router_count, -1));
        m_express_distance.assign(
            router_count, std::vector<uint32_t>(router_count, infinity));
        for (int destination = 0; destination < router_count; ++destination) {
            std::vector<uint32_t> distance(router_count, infinity);
            using QueueEntry = std::pair<uint32_t, int>;
            std::priority_queue<QueueEntry, std::vector<QueueEntry>,
                                std::greater<QueueEntry>> queue;
            distance[destination] = 0;
            queue.emplace(0, destination);
            while (!queue.empty()) {
                const auto [current_distance, router] = queue.top();
                queue.pop();
                if (current_distance != distance[router]) {
                    continue;
                }
                for (const auto &[neighbor, latency] : adjacency[router]) {
                    const uint32_t candidate = current_distance + latency;
                    if (candidate < distance[neighbor]) {
                        distance[neighbor] = candidate;
                        queue.emplace(candidate, neighbor);
                    }
                }
            }
            for (int source = 0; source < router_count; ++source) {
                m_express_distance[source][destination] = distance[source];
                if (source == destination) {
                    continue;
                }
                int next_hop = -1;
                for (const auto &[neighbor, latency] : adjacency[source]) {
                    if (distance[neighbor] != infinity &&
                        latency + distance[neighbor] == distance[source] &&
                        (next_hop == -1 || neighbor < next_hop)) {
                        next_hop = neighbor;
                    }
                }
                fatal_if(next_hop == -1,
                         "No route from router %d to router %d",
                         source, destination);
                m_express_next_hop[source][destination] = next_hop;
            }
        }
    } else {
        m_num_rows = -1;
        m_num_cols = -1;
    }

    // FaultModel: declare each router to the fault model
    if (isFaultModelEnabled()) {
        for (std::vector<Router*>::const_iterator i= m_routers.begin();
             i != m_routers.end(); ++i) {
            Router* router = safe_cast<Router*>(*i);
            [[maybe_unused]] int router_id =
                fault_model->declare_router(router->get_num_inports(),
                                            router->get_num_outports(),
                                            router->get_vc_per_vnet(),
                                            getBuffersPerDataVC(),
                                            getBuffersPerCtrlVC());
            assert(router_id == router->get_id());
            router->printAggregateFaultProbability(std::cout);
            router->printFaultVector(std::cout);
        }
    }
}

int
GarnetNetwork::getExpressNextHop(int source, int destination) const
{
    assert(source >= 0 && source < m_express_next_hop.size());
    assert(destination >= 0 && destination < m_express_next_hop.size());
    assert(source != destination);
    const int next_hop = m_express_next_hop[source][destination];
    assert(next_hop >= 0);
    return next_hop;
}

const std::vector<int>&
GarnetNetwork::getExpressNeighbors(int router) const
{
    assert(router >= 0 && router < m_express_neighbors.size());
    return m_express_neighbors[router];
}

uint32_t
GarnetNetwork::getExpressDistance(int source, int destination) const
{
    return m_express_distance.at(source).at(destination);
}

uint32_t
GarnetNetwork::getExpressEdgeLatency(int source, int destination) const
{
    return m_express_edge_latency.at(source).at(destination);
}

int
GarnetNetwork::getExpressRouteSource(int directed_id) const
{
    fatal_if(directed_id < 0 || directed_id >= 2 * m_express_link_latencies.size(),
             "invalid directed express id %d", directed_id);
    const int edge = directed_id / 2;
    return (directed_id % 2 == 0) ? m_express_link_endpoints[2 * edge]
                                  : m_express_link_endpoints[2 * edge + 1];
}

int
GarnetNetwork::getExpressRouteDestination(int directed_id) const
{
    fatal_if(directed_id < 0 || directed_id >= 2 * m_express_link_latencies.size(),
             "invalid directed express id %d", directed_id);
    const int edge = directed_id / 2;
    return (directed_id % 2 == 0) ? m_express_link_endpoints[2 * edge + 1]
                                  : m_express_link_endpoints[2 * edge];
}

double
GarnetNetwork::expressQueue(int directed_id) const
{
    const int src = getExpressRouteSource(directed_id);
    const int dst = getExpressRouteDestination(directed_id);
    return m_routers.at(src)->expressOutputQueue(dst);
}

void
GarnetNetwork::initializeSourceRoute(RouteInfo &route)
{
    if (!m_source_route_enabled)
        return;

    const int router_count = m_routers.size();
    fatal_if(route.src_router < 0 || route.src_router >= router_count ||
             route.dest_router < 0 || route.dest_router >= router_count,
             "invalid source-route pair (%d, %d)", route.src_router,
             route.dest_router);
    if (route.src_router == route.dest_router) {
        route.source_routed = true;
        route.express_count = 0;
        route.express_stage = 0;
        route.express_ids.clear();
        return;
    }
    const size_t pair_index = static_cast<size_t>(route.src_router) *
        router_count + route.dest_router;
    sampleExpressState();
    fatal_if(m_source_route_express_counts.size() !=
                 static_cast<size_t>(router_count) * router_count ||
             m_source_route_express_ids.size() !=
                 static_cast<size_t>(router_count) * router_count * 2,
             "source-route table has invalid dimensions");

    uint32_t count = m_source_route_express_counts[pair_index];
    if (m_source_route_policy != 0 &&
        m_source_route_candidate_counts.size() == router_count * router_count) {
        const uint32_t n = m_source_route_candidate_counts[pair_index];
        uint32_t best = m_source_route_policy == 3 ?
            random_mt.random<uint32_t>(0, n - 1) : 0;
        double best_cost = std::numeric_limits<double>::infinity();
        for (uint32_t c = 0; c < n && m_source_route_policy != 3; ++c) {
            const size_t base = pair_index * 8 + c;
            const uint32_t ec = m_source_route_candidate_express_counts[base];
            double cost = m_source_route_candidate_latencies[base];
            for (uint32_t i = 0; i < ec; ++i) {
                const uint32_t id = m_source_route_candidate_express_ids[base * 2 + i];
                cost += expressQueue(id);
                if (m_source_route_policy == 2)
                    cost += m_reservation_current[id];
            }
            if (cost < best_cost) { best_cost = cost; best = c; }
        }
        const size_t base = pair_index * 8 + best;
        count = m_source_route_candidate_express_counts[base];
        route.express_ids.clear();
        for (uint32_t i = 0; i < count; ++i)
            route.express_ids.push_back(m_source_route_candidate_express_ids[base * 2 + i]);
    }
    fatal_if(count > 2, "source-route express count %u exceeds limit", count);
    route.source_routed = true;
    route.express_count = count;
    route.express_stage = 0;
    std::vector<uint16_t> selected_ids = route.express_ids;
    route.express_ids.clear();
    for (uint32_t index = 0; index < count; ++index) {
        const uint32_t directed_id =
            (m_source_route_policy != 0 && !selected_ids.empty()) ?
            selected_ids[index] : m_source_route_express_ids[pair_index * 2 + index];
        fatal_if(directed_id >= 2 * m_express_link_latencies.size(),
                 "source-route directed express id %u is invalid",
                 directed_id);
        route.express_ids.push_back(directed_id);
    }
    recordSourceRouteSelection(route);
    reserveSourceRoute(route);
}

void
GarnetNetwork::reserveSourceRoute(const RouteInfo &route)
{
    if (!route.source_routed)
        return;
    for (uint16_t id : route.express_ids) {
        fatal_if(id >= m_reservation_current.size(),
                 "reservation express id %u is invalid", id);
        ++m_reservation_current[id];
        m_express_reservation_increments[id]++;
    }
}

void
GarnetNetwork::recordSourceRouteSelection(const RouteInfo &route)
{
    if (!route.source_routed)
        return;
    const unsigned count = std::min<unsigned>(route.express_count, 2);
    m_source_route_packets_planned[count]++;
    for (unsigned i = 0; i < count; ++i) {
        const auto id = route.express_ids.at(i);
        if (id < m_reservation_current.size())
            m_express_edge_packets_selected[id]++;
    }
}

void
GarnetNetwork::sampleExpressState()
{
    const size_t n = m_reservation_current.size();
    if (m_q_sample_sum.size() != n) {
        m_q_sample_sum.assign(n, 0); m_q_sample_max.assign(n, 0);
        m_r_sample_sum.assign(n, 0); m_r_sample_max.assign(n, 0);
    }
    for (size_t i = 0; i < n; ++i) {
        const uint64_t q = static_cast<uint64_t>(expressQueue(i));
        const uint64_t r = static_cast<uint64_t>(m_reservation_current[i]);
        m_q_sample_sum[i] += q; m_q_sample_max[i] = std::max(m_q_sample_max[i], q);
        m_r_sample_sum[i] += r; m_r_sample_max[i] = std::max(m_r_sample_max[i], r);
    }
    ++m_express_state_samples;
}

void
GarnetNetwork::recordDeliveredSourceRoute(const RouteInfo &route)
{
    if (route.source_routed && route.express_traversed <= 2)
        m_source_route_packets_delivered[route.express_traversed]++;
}

void
GarnetNetwork::recordEscapeTransition(RouteInfo &route, Tick tick)
{
    route.escape_transition_tick = tick;
    m_escape_vc_transitions++;
    if (route.injection_tick <= tick)
        m_escape_transition_delay_ticks += tick - route.injection_tick;
}

void
GarnetNetwork::recordDeliveredEscape(const RouteInfo &route)
{
    if (route.escape_vc)
        m_delivered_escape_packets++;
}

void
GarnetNetwork::releaseSourceRouteExpress(int directed_id)
{
    fatal_if(directed_id < 0 || directed_id >= m_reservation_current.size(),
             "release reservation express id %d is invalid", directed_id);
    fatal_if(m_reservation_current[directed_id] <= 0,
             "reservation underflow on express id %d", directed_id);
    --m_reservation_current[directed_id];
    m_express_reservation_decrements[directed_id]++;
}

/*
 * This function creates a link from the Network Interface (NI)
 * into the Network.
 * It creates a Network Link from the NI to a Router and a Credit Link from
 * the Router to the NI
*/

void
GarnetNetwork::makeExtInLink(NodeID global_src, SwitchID dest, BasicLink* link,
                             std::vector<NetDest>& routing_table_entry)
{
    NodeID local_src = getLocalNodeID(global_src);
    assert(local_src < m_nodes);

    GarnetExtLink* garnet_link = safe_cast<GarnetExtLink*>(link);

    // GarnetExtLink is bi-directional
    NetworkLink* net_link = garnet_link->m_network_links[LinkDirection_In];
    net_link->setType(EXT_IN_);
    CreditLink* credit_link = garnet_link->m_credit_links[LinkDirection_In];

    m_networklinks.push_back(net_link);
    m_creditlinks.push_back(credit_link);

    PortDirection dst_inport_dirn = "Local";

    m_max_vcs_per_vnet = std::max(m_max_vcs_per_vnet,
                             m_routers[dest]->get_vc_per_vnet());

    /*
     * We check if a bridge was enabled at any end of the link.
     * The bridge is enabled if either of clock domain
     * crossing (CDC) or Serializer-Deserializer(SerDes) unit is
     * enabled for the link at each end. The bridge encapsulates
     * the functionality for both CDC and SerDes and is a Consumer
     * object similiar to a NetworkLink.
     *
     * If a bridge was enabled we connect the NI and Routers to
     * bridge before connecting the link. Example, if an external
     * bridge is enabled, we would connect:
     * NI--->NetworkBridge--->GarnetExtLink---->Router
     */
    if (garnet_link->extBridgeEn) {
        DPRINTF(RubyNetwork, "Enable external bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->extNetBridge[LinkDirection_In];
        m_nis[local_src]->
        addOutPort(n_bridge,
                   garnet_link->extCredBridge[LinkDirection_In],
                   dest, m_routers[dest]->get_vc_per_vnet());
        m_networkbridges.push_back(n_bridge);
    } else {
        m_nis[local_src]->addOutPort(net_link, credit_link, dest,
            m_routers[dest]->get_vc_per_vnet());
    }

    if (garnet_link->intBridgeEn) {
        DPRINTF(RubyNetwork, "Enable internal bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->intNetBridge[LinkDirection_In];
        m_routers[dest]->
            addInPort(dst_inport_dirn,
                      n_bridge,
                      garnet_link->intCredBridge[LinkDirection_In]);
        m_networkbridges.push_back(n_bridge);
    } else {
        m_routers[dest]->addInPort(dst_inport_dirn, net_link, credit_link);
    }

}

/*
 * This function creates a link from the Network to a NI.
 * It creates a Network Link from a Router to the NI and
 * a Credit Link from NI to the Router
*/

void
GarnetNetwork::makeExtOutLink(SwitchID src, NodeID global_dest,
                              BasicLink* link,
                              std::vector<NetDest>& routing_table_entry)
{
    NodeID local_dest = getLocalNodeID(global_dest);
    assert(local_dest < m_nodes);
    assert(src < m_routers.size());
    assert(m_routers[src] != NULL);

    GarnetExtLink* garnet_link = safe_cast<GarnetExtLink*>(link);

    // GarnetExtLink is bi-directional
    NetworkLink* net_link = garnet_link->m_network_links[LinkDirection_Out];
    net_link->setType(EXT_OUT_);
    CreditLink* credit_link = garnet_link->m_credit_links[LinkDirection_Out];

    m_networklinks.push_back(net_link);
    m_creditlinks.push_back(credit_link);

    PortDirection src_outport_dirn = "Local";

    m_max_vcs_per_vnet = std::max(m_max_vcs_per_vnet,
                             m_routers[src]->get_vc_per_vnet());

    /*
     * We check if a bridge was enabled at any end of the link.
     * The bridge is enabled if either of clock domain
     * crossing (CDC) or Serializer-Deserializer(SerDes) unit is
     * enabled for the link at each end. The bridge encapsulates
     * the functionality for both CDC and SerDes and is a Consumer
     * object similiar to a NetworkLink.
     *
     * If a bridge was enabled we connect the NI and Routers to
     * bridge before connecting the link. Example, if an external
     * bridge is enabled, we would connect:
     * NI<---NetworkBridge<---GarnetExtLink<----Router
     */
    if (garnet_link->extBridgeEn) {
        DPRINTF(RubyNetwork, "Enable external bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->extNetBridge[LinkDirection_Out];
        m_nis[local_dest]->
            addInPort(n_bridge, garnet_link->extCredBridge[LinkDirection_Out]);
        m_networkbridges.push_back(n_bridge);
    } else {
        m_nis[local_dest]->addInPort(net_link, credit_link);
    }

    if (garnet_link->intBridgeEn) {
        DPRINTF(RubyNetwork, "Enable internal bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->intNetBridge[LinkDirection_Out];
        m_routers[src]->
            addOutPort(src_outport_dirn,
                       n_bridge,
                       routing_table_entry, link->m_weight,
                       garnet_link->intCredBridge[LinkDirection_Out],
                       m_routers[src]->get_vc_per_vnet());
        m_networkbridges.push_back(n_bridge);
    } else {
        m_routers[src]->
            addOutPort(src_outport_dirn, net_link,
                       routing_table_entry,
                       link->m_weight, credit_link,
                       m_routers[src]->get_vc_per_vnet());
    }
}

/*
 * This function creates an internal network link between two routers.
 * It adds both the network link and an opposite credit link.
*/

void
GarnetNetwork::makeInternalLink(SwitchID src, SwitchID dest, BasicLink* link,
                                std::vector<NetDest>& routing_table_entry,
                                PortDirection src_outport_dirn,
                                PortDirection dst_inport_dirn)
{
    GarnetIntLink* garnet_link = safe_cast<GarnetIntLink*>(link);

    // GarnetIntLink is unidirectional
    NetworkLink* net_link = garnet_link->m_network_link;
    net_link->setType(INT_);
    CreditLink* credit_link = garnet_link->m_credit_link;

    m_networklinks.push_back(net_link);
    m_creditlinks.push_back(credit_link);

    m_max_vcs_per_vnet = std::max(m_max_vcs_per_vnet,
                             std::max(m_routers[dest]->get_vc_per_vnet(),
                             m_routers[src]->get_vc_per_vnet()));

    /*
     * We check if a bridge was enabled at any end of the link.
     * The bridge is enabled if either of clock domain
     * crossing (CDC) or Serializer-Deserializer(SerDes) unit is
     * enabled for the link at each end. The bridge encapsulates
     * the functionality for both CDC and SerDes and is a Consumer
     * object similiar to a NetworkLink.
     *
     * If a bridge was enabled we connect the NI and Routers to
     * bridge before connecting the link. Example, if a source
     * bridge is enabled, we would connect:
     * Router--->NetworkBridge--->GarnetIntLink---->Router
     */
    if (garnet_link->dstBridgeEn) {
        DPRINTF(RubyNetwork, "Enable destination bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->dstNetBridge;
        m_routers[dest]->addInPort(dst_inport_dirn, n_bridge,
                                   garnet_link->dstCredBridge);
        m_networkbridges.push_back(n_bridge);
    } else {
        m_routers[dest]->addInPort(dst_inport_dirn, net_link, credit_link);
    }

    if (garnet_link->srcBridgeEn) {
        DPRINTF(RubyNetwork, "Enable source bridge for %s\n",
            garnet_link->name());
        NetworkBridge *n_bridge = garnet_link->srcNetBridge;
        m_routers[src]->
            addOutPort(src_outport_dirn, n_bridge,
                       routing_table_entry,
                       link->m_weight, garnet_link->srcCredBridge,
                       m_routers[dest]->get_vc_per_vnet());
        m_networkbridges.push_back(n_bridge);
    } else {
        m_routers[src]->addOutPort(src_outport_dirn, net_link,
                        routing_table_entry,
                        link->m_weight, credit_link,
                        m_routers[dest]->get_vc_per_vnet());
    }
}

// Total routers in the network
int
GarnetNetwork::getNumRouters()
{
    return m_routers.size();
}

// Get ID of router connected to a NI.
int
GarnetNetwork::get_router_id(int global_ni, int vnet)
{
    NodeID local_ni = getLocalNodeID(global_ni);

    return m_nis[local_ni]->get_router_id(vnet);
}

void
GarnetNetwork::regStats()
{
    Network::regStats();

    // Packets
    m_packets_received
        .init(m_virtual_networks)
        .name(name() + ".packets_received")
        .flags(statistics::pdf | statistics::total | statistics::nozero |
            statistics::oneline)
        ;

    m_packets_injected
        .init(m_virtual_networks)
        .name(name() + ".packets_injected")
        .flags(statistics::pdf | statistics::total | statistics::nozero |
            statistics::oneline)
        ;

    m_packet_network_latency
        .init(m_virtual_networks)
        .name(name() + ".packet_network_latency")
        .flags(statistics::oneline)
        ;

    m_packet_queueing_latency
        .init(m_virtual_networks)
        .name(name() + ".packet_queueing_latency")
        .flags(statistics::oneline)
        ;

    for (int i = 0; i < m_virtual_networks; i++) {
        m_packets_received.subname(i, csprintf("vnet-%i", i));
        m_packets_injected.subname(i, csprintf("vnet-%i", i));
        m_packet_network_latency.subname(i, csprintf("vnet-%i", i));
        m_packet_queueing_latency.subname(i, csprintf("vnet-%i", i));
    }

    m_avg_packet_vnet_latency
        .name(name() + ".average_packet_vnet_latency")
        .flags(statistics::oneline);
    m_avg_packet_vnet_latency =
        m_packet_network_latency / m_packets_received;

    m_avg_packet_vqueue_latency
        .name(name() + ".average_packet_vqueue_latency")
        .flags(statistics::oneline);
    m_avg_packet_vqueue_latency =
        m_packet_queueing_latency / m_packets_received;

    m_avg_packet_network_latency
        .name(name() + ".average_packet_network_latency");
    m_avg_packet_network_latency =
        sum(m_packet_network_latency) / sum(m_packets_received);

    m_avg_packet_queueing_latency
        .name(name() + ".average_packet_queueing_latency");
    m_avg_packet_queueing_latency
        = sum(m_packet_queueing_latency) / sum(m_packets_received);

    m_avg_packet_latency
        .name(name() + ".average_packet_latency");
    m_avg_packet_latency
        = m_avg_packet_network_latency + m_avg_packet_queueing_latency;

    // Flits
    m_flits_received
        .init(m_virtual_networks)
        .name(name() + ".flits_received")
        .flags(statistics::pdf | statistics::total | statistics::nozero |
            statistics::oneline)
        ;

    m_flits_injected
        .init(m_virtual_networks)
        .name(name() + ".flits_injected")
        .flags(statistics::pdf | statistics::total | statistics::nozero |
            statistics::oneline)
        ;

    m_flit_network_latency
        .init(m_virtual_networks)
        .name(name() + ".flit_network_latency")
        .flags(statistics::oneline)
        ;

    m_flit_queueing_latency
        .init(m_virtual_networks)
        .name(name() + ".flit_queueing_latency")
        .flags(statistics::oneline)
        ;

    for (int i = 0; i < m_virtual_networks; i++) {
        m_flits_received.subname(i, csprintf("vnet-%i", i));
        m_flits_injected.subname(i, csprintf("vnet-%i", i));
        m_flit_network_latency.subname(i, csprintf("vnet-%i", i));
        m_flit_queueing_latency.subname(i, csprintf("vnet-%i", i));
    }

    m_avg_flit_vnet_latency
        .name(name() + ".average_flit_vnet_latency")
        .flags(statistics::oneline);
    m_avg_flit_vnet_latency = m_flit_network_latency / m_flits_received;

    m_avg_flit_vqueue_latency
        .name(name() + ".average_flit_vqueue_latency")
        .flags(statistics::oneline);
    m_avg_flit_vqueue_latency =
        m_flit_queueing_latency / m_flits_received;

    m_avg_flit_network_latency
        .name(name() + ".average_flit_network_latency");
    m_avg_flit_network_latency =
        sum(m_flit_network_latency) / sum(m_flits_received);

    m_avg_flit_queueing_latency
        .name(name() + ".average_flit_queueing_latency");
    m_avg_flit_queueing_latency =
        sum(m_flit_queueing_latency) / sum(m_flits_received);

    m_avg_flit_latency
        .name(name() + ".average_flit_latency");
    m_avg_flit_latency =
        m_avg_flit_network_latency + m_avg_flit_queueing_latency;


    // Hops
    m_avg_hops.name(name() + ".average_hops");
    m_avg_hops = m_total_hops / sum(m_flits_received);
    m_express_link_traversals
        .name(name() + ".express_link_traversals");
    m_escape_vc_traversals.name(name() + ".escape_vc_traversals");
    m_escape_vc_transitions.name(name() + ".escape_vc_transitions");
    m_escape_transition_delay_ticks.name(name() + ".escape_transition_delay_ticks");
    m_delivered_escape_packets.name(name() + ".delivered_escape_packets");
    m_nonminimal_route_decisions.name(name() + ".nonminimal_route_decisions");
    const size_t reservation_stat_size =
        std::max<size_t>(1, m_reservation_current.size());
    m_express_reservation_current
        .init(reservation_stat_size)
        .name(name() + ".express_reservation_current")
        .flags(statistics::oneline);
    m_express_reservation_increments
        .init(reservation_stat_size)
        .name(name() + ".express_reservation_increments")
        .flags(statistics::oneline);
    m_express_reservation_decrements
        .init(reservation_stat_size)
        .name(name() + ".express_reservation_decrements")
        .flags(statistics::oneline);
    m_source_route_packets_planned
        .init(3).name(name() + ".source_route_packets_planned")
        .flags(statistics::oneline);
    m_source_route_packets_delivered
        .init(3).name(name() + ".source_route_packets_delivered")
        .flags(statistics::oneline);
    m_express_edge_packets_selected
        .init(reservation_stat_size)
        .name(name() + ".express_edge_packets_selected")
        .flags(statistics::oneline);
    m_express_q_sample_sum.init(reservation_stat_size)
        .name(name() + ".express_q_sample_sum").flags(statistics::oneline);
    m_express_q_sample_max.init(reservation_stat_size)
        .name(name() + ".express_q_sample_max").flags(statistics::oneline);
    m_express_r_sample_sum.init(reservation_stat_size)
        .name(name() + ".express_r_sample_sum").flags(statistics::oneline);
    m_express_r_sample_max.init(reservation_stat_size)
        .name(name() + ".express_r_sample_max").flags(statistics::oneline);
    m_express_state_sample_count.name(name() + ".express_state_sample_count");

    int int_links = 0;
    for (auto *link : m_networklinks) {
        if (link->getType() == INT_)
            ++int_links;
    }
    m_int_link_utilization
        .init(int_links)
        .name(name() + ".express_mesh_int_link_utilization")
        .flags(statistics::oneline);

    // Links
    m_total_ext_in_link_utilization
        .name(name() + ".ext_in_link_utilization");
    m_total_ext_out_link_utilization
        .name(name() + ".ext_out_link_utilization");
    m_total_int_link_utilization
        .name(name() + ".int_link_utilization");
    m_average_link_utilization
        .name(name() + ".avg_link_utilization");
    m_average_vc_load
        .init(m_virtual_networks * m_max_vcs_per_vnet)
        .name(name() + ".avg_vc_load")
        .flags(statistics::pdf | statistics::total | statistics::nozero |
            statistics::oneline)
        ;

    // Traffic distribution
    for (int source = 0; source < m_routers.size(); ++source) {
        m_data_traffic_distribution.push_back(
            std::vector<statistics::Scalar *>());
        m_ctrl_traffic_distribution.push_back(
            std::vector<statistics::Scalar *>());

        for (int dest = 0; dest < m_routers.size(); ++dest) {
            statistics::Scalar *data_packets = new statistics::Scalar();
            statistics::Scalar *ctrl_packets = new statistics::Scalar();

            data_packets->name(name() + ".data_traffic_distribution." + "n" +
                    std::to_string(source) + "." + "n" + std::to_string(dest));
            m_data_traffic_distribution[source].push_back(data_packets);

            ctrl_packets->name(name() + ".ctrl_traffic_distribution." + "n" +
                    std::to_string(source) + "." + "n" + std::to_string(dest));
            m_ctrl_traffic_distribution[source].push_back(ctrl_packets);
        }
    }
}

void
GarnetNetwork::collateStats()
{
    RubySystem *rs = params().ruby_system;
    double time_delta = double(curCycle() - rs->getStartCycle());

    int int_link_index = 0;
    for (int i = 0; i < m_networklinks.size(); i++) {
        link_type type = m_networklinks[i]->getType();
        int activity = m_networklinks[i]->getLinkUtilization();

        if (type == EXT_IN_)
            m_total_ext_in_link_utilization += activity;
        else if (type == EXT_OUT_)
            m_total_ext_out_link_utilization += activity;
        else if (type == INT_) {
            m_total_int_link_utilization += activity;
            m_int_link_utilization[int_link_index++] +=
                double(activity) / time_delta;
        }

        m_average_link_utilization +=
            (double(activity) / time_delta);

        std::vector<unsigned int> vc_load = m_networklinks[i]->getVcLoad();
        for (int j = 0; j < vc_load.size(); j++) {
            m_average_vc_load[j] += ((double)vc_load[j] / time_delta);
        }
    }

    for (size_t i = 0; i < m_reservation_current.size(); ++i) {
        m_express_reservation_current[i] = m_reservation_current[i];
        m_express_q_sample_sum[i] = m_q_sample_sum[i];
        m_express_q_sample_max[i] = m_q_sample_max[i];
        m_express_r_sample_sum[i] = m_r_sample_sum[i];
        m_express_r_sample_max[i] = m_r_sample_max[i];
    }
    m_express_state_sample_count = m_express_state_samples;

    // Ask the routers to collate their statistics
    for (int i = 0; i < m_routers.size(); i++) {
        m_routers[i]->collateStats();
    }
}

void
GarnetNetwork::resetStats()
{
    // Ruby resets statistics at the warmup boundary, while source-route
    // reservation state must remain live.  Clear only measurement accumulators
    // so q/r samples and packet-selection counters do not include warmup.
    std::fill(m_q_sample_sum.begin(), m_q_sample_sum.end(), 0);
    std::fill(m_q_sample_max.begin(), m_q_sample_max.end(), 0);
    std::fill(m_r_sample_sum.begin(), m_r_sample_sum.end(), 0);
    std::fill(m_r_sample_max.begin(), m_r_sample_max.end(), 0);
    m_express_state_samples = 0;
    for (int i = 0; i < m_routers.size(); i++) {
        m_routers[i]->resetStats();
    }
    for (int i = 0; i < m_networklinks.size(); i++) {
        m_networklinks[i]->resetStats();
    }
    for (int i = 0; i < m_creditlinks.size(); i++) {
        m_creditlinks[i]->resetStats();
    }
}

void
GarnetNetwork::print(std::ostream& out) const
{
    out << "[GarnetNetwork]";
}

void
GarnetNetwork::update_traffic_distribution(RouteInfo route)
{
    int src_node = route.src_router;
    int dest_node = route.dest_router;
    int vnet = route.vnet;

    if (m_vnet_type[vnet] == DATA_VNET_)
        (*m_data_traffic_distribution[src_node][dest_node])++;
    else
        (*m_ctrl_traffic_distribution[src_node][dest_node])++;
}

bool
GarnetNetwork::functionalRead(Packet *pkt, WriteMask &mask)
{
    bool read = false;
    for (unsigned int i = 0; i < m_routers.size(); i++) {
        if (m_routers[i]->functionalRead(pkt, mask))
            read = true;
    }

    for (unsigned int i = 0; i < m_nis.size(); ++i) {
        if (m_nis[i]->functionalRead(pkt, mask))
            read = true;
    }

    for (unsigned int i = 0; i < m_networklinks.size(); ++i) {
        if (m_networklinks[i]->functionalRead(pkt, mask))
            read = true;
    }

    for (unsigned int i = 0; i < m_networkbridges.size(); ++i) {
        if (m_networkbridges[i]->functionalRead(pkt, mask))
            read = true;
    }

    return read;
}

uint32_t
GarnetNetwork::functionalWrite(Packet *pkt)
{
    uint32_t num_functional_writes = 0;

    for (unsigned int i = 0; i < m_routers.size(); i++) {
        num_functional_writes += m_routers[i]->functionalWrite(pkt);
    }

    for (unsigned int i = 0; i < m_nis.size(); ++i) {
        num_functional_writes += m_nis[i]->functionalWrite(pkt);
    }

    for (unsigned int i = 0; i < m_networklinks.size(); ++i) {
        num_functional_writes += m_networklinks[i]->functionalWrite(pkt);
    }

    return num_functional_writes;
}

} // namespace garnet
} // namespace ruby
} // namespace gem5
