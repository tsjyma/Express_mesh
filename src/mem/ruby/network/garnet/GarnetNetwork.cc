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
#include <cstdlib>
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
#include "mem/ruby/network/garnet/InputUnit.hh"
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
    : Network(p),
      m_express_info_last_cycle(std::numeric_limits<uint64_t>::max()),
      m_express_info_event(
          [this]{ processExpressInfoEvent(); },
          "Garnet express pressure advertisement")
{
    m_num_rows = p.num_rows;
    m_ni_flit_size = p.ni_flit_size;
    m_max_vcs_per_vnet = 0;
    m_buffers_per_data_vc = p.buffers_per_data_vc;
    m_buffers_per_ctrl_vc = p.buffers_per_ctrl_vc;
    m_routing_algorithm = p.routing_algorithm;
    m_express_link_endpoints = p.express_link_endpoints;
    m_express_link_latencies = p.express_link_latencies;
    m_source_route_enabled = p.source_route_enabled;
    m_source_route_express_counts = p.source_route_express_counts;
    m_source_route_express_ids = p.source_route_express_ids;
    m_source_route_candidate_counts = p.source_route_candidate_counts;
    m_source_route_candidate_latencies = p.source_route_candidate_latencies;
    m_source_route_candidate_express_counts = p.source_route_candidate_express_counts;
    m_source_route_candidate_express_ids = p.source_route_candidate_express_ids;
    m_source_route_candidates = p.source_route_candidates;
    m_source_route_policy = p.source_route_policy;
    m_source_route_mesh_routing = p.source_route_mesh_routing;
    m_source_route_mesh_link_latency = p.source_route_mesh_link_latency;
    m_source_route_reservation_weight = p.source_route_reservation_weight;
    m_source_route_vc_weight = p.source_route_vc_weight;
    m_source_route_info_mode = p.source_route_info_mode;
    m_source_route_reservation_mode = p.source_route_reservation_mode;
    m_source_route_info_period = p.source_route_info_period;
    m_source_route_info_delay = p.source_route_info_delay;
    m_source_route_info_bits = p.source_route_info_bits;
    m_source_route_admission_fraction = p.source_route_admission_fraction;
    m_reservation_current.assign(2 * m_express_link_latencies.size(), 0);
    const size_t configured_router_count =
        static_cast<size_t>(p.num_rows) * p.num_rows;
    m_directed_link_reservations.assign(
        configured_router_count * configured_router_count, 0);
    m_deadlock_state_dumped = false;
    m_express_escape_timeout = p.express_escape_timeout;
    m_express_escape_enabled = p.express_escape_enabled;
    m_next_packet_id = 0;

    fatal_if(m_source_route_policy != 0 && m_source_route_policy != 3 &&
             m_source_route_policy != 4 && m_source_route_policy != 5 &&
             m_source_route_policy != 6,
             "source-route policy %u is invalid; supported policies are "
             "0 (static), 3 (random top-K), 4 (q/r pressure-aware), "
             "5 (express Dijkstra), and 6 (global Dijkstra)",
             m_source_route_policy);
    fatal_if(m_source_route_mesh_routing != "xy" &&
             m_source_route_mesh_routing != "adaptive",
             "source-route mesh routing %s is invalid",
             m_source_route_mesh_routing.c_str());
    fatal_if(m_source_route_candidates == 0,
             "source-route candidate count K must be positive");
    fatal_if(m_source_route_reservation_weight < 0.0 ||
             m_source_route_vc_weight < 0.0,
             "source-route pressure weights must be non-negative");
    fatal_if(m_source_route_info_mode != "instant" &&
             m_source_route_info_mode != "distance-gossip",
             "invalid express information mode %s",
             m_source_route_info_mode.c_str());
    fatal_if(m_source_route_reservation_mode != "instant" &&
             m_source_route_reservation_mode != "registered",
             "invalid express reservation mode %s",
             m_source_route_reservation_mode.c_str());
    fatal_if(m_source_route_reservation_mode == "registered" &&
             (m_source_route_info_mode != "distance-gossip" ||
              m_source_route_info_delay == 0 ||
              m_source_route_policy != 4),
             "registered reservations require policy 4 and distance-gossip "
             "with at least one cycle of base delay");
    fatal_if(m_source_route_info_period == 0,
             "express information period must be positive");
    fatal_if(m_source_route_info_bits > 4,
             "express information bits must be in 0..4");
    fatal_if(m_source_route_admission_fraction < 0.0 ||
             m_source_route_admission_fraction > 1.0,
             "express admission fraction must be in [0,1]");
    fatal_if(m_express_escape_enabled && p.vcs_per_vnet < 2,
             "escape routing needs at least two VCs per vnet");

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
    m_local_unacknowledged.assign(
        m_routers.size(),
        std::vector<uint32_t>(m_reservation_current.size(), 0));
    m_router_last_wakeup_cycle.assign(
        m_routers.size(), std::numeric_limits<uint64_t>::max());

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
GarnetNetwork::startup()
{
    if (m_source_route_info_mode != "instant" ||
        m_source_route_reservation_mode == "registered")
        schedule(m_express_info_event, clockEdge(Cycles(1)));
}

void
GarnetNetwork::processExpressInfoEvent()
{
    m_express_info_event_last_cycle = curCycle();
    processReservationControl();
    captureExpressInformation();
    schedule(m_express_info_event, clockEdge(Cycles(1)));
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
        for (size_t index = 0; index < m_express_link_latencies.size();
             ++index) {
            const int u = m_express_link_endpoints[2 * index];
            const int v = m_express_link_endpoints[2 * index + 1];
            fatal_if(u < 0 || u >= router_count || v < 0 ||
                         v >= router_count,
                     "Express-link endpoint outside router range");
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
GarnetNetwork::expressVcOccupancy(int directed_id, int vnet)
{
    const int src = getExpressRouteSource(directed_id);
    const int dst = getExpressRouteDestination(directed_id);
    return m_routers.at(src)->expressOutputVcOccupancy(dst, vnet);
}

uint32_t
GarnetNetwork::quantizeExpressPressure(uint64_t value) const
{
    if (m_source_route_info_bits == 0) {
        return static_cast<uint32_t>(std::min<uint64_t>(
            value, std::numeric_limits<uint32_t>::max()));
    }
    if (m_source_route_info_bits == 1)
        return value ? 1 : 0;
    if (m_source_route_info_bits == 2) {
        if (value == 0)
            return 0;
        if (value == 1)
            return 1;
        return value <= 3 ? 2 : 4;
    }
    const uint32_t maximum =
        (uint32_t(1) << m_source_route_info_bits) - 1;
    return static_cast<uint32_t>(std::min<uint64_t>(value, maximum));
}

void
GarnetNetwork::captureExpressInformation()
{
    if (m_source_route_info_mode == "instant")
        return;
    const uint64_t now = curCycle();
    if (now == m_express_info_last_cycle)
        return;
    m_express_info_last_cycle = now;
    if (now % m_source_route_info_period != 0)
        return;
    if (m_express_info_event_last_cycle != now) {
        ++m_express_info_snapshots_before_periodic_event;
        ++m_express_info_snapshots_before_periodic_by_parity[now % 2];
    }
    m_express_info_snapshot_router_wakeups_sum += std::count(
        m_router_last_wakeup_cycle.begin(), m_router_last_wakeup_cycle.end(),
        now);

    const size_t directed_count = m_reservation_current.size();
    ExpressInfoSnapshot snapshot;
    snapshot.cycle = now;
    snapshot.q.resize(directed_count * m_virtual_networks);
    snapshot.r.resize(directed_count);
    for (size_t id = 0; id < directed_count; ++id) {
        snapshot.r[id] = quantizeExpressPressure(
            std::max<int64_t>(0, m_reservation_current[id]));
        for (int vnet = 0; vnet < m_virtual_networks; ++vnet) {
            snapshot.q[vnet * directed_count + id] =
                quantizeExpressPressure(static_cast<uint64_t>(
                    expressVcOccupancy(id, vnet)));
        }
    }
    m_express_info_history.push_back(std::move(snapshot));
    const uint64_t maximum_age = m_source_route_info_delay +
        2 * m_routers.size() + m_source_route_info_period + 4;
    while (!m_express_info_history.empty() &&
           m_express_info_history.front().cycle + maximum_age < now) {
        m_express_info_history.pop_front();
    }
}

std::pair<double, double>
GarnetNetwork::observedExpressPressure(
    int source, int directed_id, int vnet)
{
    const uint64_t now = curCycle();
    if (m_express_info_event_last_cycle != now)
        ++m_express_info_queries_before_periodic_event;
    const int entry = getExpressRouteSource(directed_id);
    if (m_router_last_wakeup_cycle.at(entry) == now)
        ++m_express_info_queries_after_entry_router_wakeup;
    const double true_q = expressVcOccupancy(directed_id, vnet);
    const double true_r = static_cast<double>(
        m_reservation_current[directed_id]);
    const double local_pending =
        m_source_route_reservation_mode == "registered" ?
        m_local_unacknowledged.at(source).at(directed_id) : 0.0;
    std::pair<double, double> result;
    if (m_source_route_info_mode == "instant") {
        result = {true_q, true_r};
    } else {
        captureExpressInformation();
        uint64_t delay = m_source_route_info_delay;
        if (m_source_route_info_mode == "distance-gossip") {
            delay += std::abs(source % m_num_cols - entry % m_num_cols) +
                     std::abs(source / m_num_cols - entry / m_num_cols);
        }
        result = {0.0, local_pending};
        if (delay <= now) {
            const uint64_t target = now - delay;
            const size_t directed_count = m_reservation_current.size();
            for (auto item = m_express_info_history.rbegin();
                 item != m_express_info_history.rend(); ++item) {
                if (item->cycle <= target) {
                    result.first = static_cast<double>(
                        item->q[vnet * directed_count + directed_id]);
                    result.second = static_cast<double>(
                        item->r[directed_id]) + local_pending;
                    break;
                }
            }
        }
    }
    ++m_express_info_queries;
    m_express_info_true_q_sum += true_q;
    m_express_info_observed_q_sum += result.first;
    m_express_info_true_r_sum += true_r;
    m_express_info_observed_r_sum += result.second;
    m_express_info_local_pending_sum += local_pending;
    return result;
}

void
GarnetNetwork::recordRouterWakeup(int router_id)
{
    m_router_last_wakeup_cycle.at(router_id) = curCycle();
}

bool
GarnetNetwork::admitExpressRoute(int source, int destination) const
{
    if (m_source_route_admission_fraction >= 1.0)
        return true;
    if (m_source_route_admission_fraction <= 0.0)
        return false;
    uint64_t value = static_cast<uint64_t>(m_next_packet_id) ^
        (static_cast<uint64_t>(source) << 32) ^
        static_cast<uint64_t>(destination);
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    value ^= value >> 31;
    const long double sample = static_cast<long double>(value) /
        static_cast<long double>(std::numeric_limits<uint64_t>::max());
    return sample < m_source_route_admission_fraction;
}

int
GarnetNetwork::directedExpressId(int source, int destination) const
{
    for (size_t edge = 0; edge < m_express_link_latencies.size(); ++edge) {
        const int first = m_express_link_endpoints[edge * 2];
        const int second = m_express_link_endpoints[edge * 2 + 1];
        if (source == first && destination == second)
            return static_cast<int>(edge * 2);
        if (source == second && destination == first)
            return static_cast<int>(edge * 2 + 1);
    }
    return -1;
}

std::vector<uint16_t>
GarnetNetwork::dynamicDijkstraRoute(
    int source, int destination, int vnet, bool all_link_pressure) const
{
    const int router_count = m_routers.size();
    const double infinity = std::numeric_limits<double>::infinity();
    std::vector<double> distance(router_count, infinity);
    std::vector<int> previous(router_count, -1);
    using Item = std::pair<double, int>;
    std::priority_queue<Item, std::vector<Item>, std::greater<Item>> ready;
    distance[source] = 0.0;
    ready.push({0.0, source});

    auto relax = [&](int node, int next, uint32_t static_latency,
                     auto &queue) {
        const int express_id = directedExpressId(node, next);
        double weight = static_latency;
        if (express_id >= 0 || all_link_pressure) {
            const int64_t reserved = all_link_pressure ?
                m_directed_link_reservations[
                    static_cast<size_t>(node) * router_count + next] :
                m_reservation_current[express_id];
            weight += m_source_route_reservation_weight *
                std::max<int64_t>(reserved, 0);
            weight += m_source_route_vc_weight *
                m_routers[node]->outputVcOccupancyTo(next, vnet);
        }
        const double candidate = distance[node] + weight;
        if (candidate < distance[next]) {
            distance[next] = candidate;
            previous[next] = node;
            queue.push({candidate, next});
        }
    };

    while (!ready.empty()) {
        const auto [cost, node] = ready.top();
        ready.pop();
        if (cost != distance[node])
            continue;
        if (node == destination)
            break;
        const int x = node % m_num_cols;
        const int y = node / m_num_cols;
        if (x > 0)
            relax(node, node - 1, m_source_route_mesh_link_latency, ready);
        if (x + 1 < m_num_cols)
            relax(node, node + 1, m_source_route_mesh_link_latency, ready);
        if (y > 0)
            relax(node, node - m_num_cols,
                  m_source_route_mesh_link_latency, ready);
        if (y + 1 < m_num_rows)
            relax(node, node + m_num_cols,
                  m_source_route_mesh_link_latency, ready);
        for (size_t edge = 0; edge < m_express_link_latencies.size(); ++edge) {
            const int first = m_express_link_endpoints[edge * 2];
            const int second = m_express_link_endpoints[edge * 2 + 1];
            if (node == first)
                relax(node, second, m_express_link_latencies[edge], ready);
            else if (node == second)
                relax(node, first, m_express_link_latencies[edge], ready);
        }
    }
    fatal_if(previous[destination] < 0,
             "dynamic Dijkstra could not route %d to %d", source,
             destination);
    std::vector<uint16_t> reversed;
    for (int node = destination; node != source; node = previous[node])
        reversed.push_back(static_cast<uint16_t>(node));
    std::reverse(reversed.begin(), reversed.end());
    return reversed;
}

void
GarnetNetwork::initializeSourceRoute(RouteInfo &route)
{
    if (!m_source_route_enabled)
        return;
    // NetworkInterface allocates this exact packet ID immediately after
    // source-route initialization.  Keeping it in RouteInfo makes each
    // reservation stage uniquely identifiable at later routers.
    route.source_route_packet_id = static_cast<uint64_t>(m_next_packet_id);

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
    if (m_source_route_policy == 5 || m_source_route_policy == 6) {
        route.dynamic_route_routers = dynamicDijkstraRoute(
            route.src_router, route.dest_router, route.vnet,
            m_source_route_policy == 6);
        route.dynamic_route_stage = 0;
        route.express_ids.clear();
        int current = route.src_router;
        for (const uint16_t next : route.dynamic_route_routers) {
            const int express_id = directedExpressId(current, next);
            if (express_id >= 0)
                route.express_ids.push_back(express_id);
            if (m_source_route_policy == 6) {
                ++m_directed_link_reservations[
                    static_cast<size_t>(current) * router_count + next];
            }
            current = next;
        }
        count = route.express_ids.size();
    }
    if (m_source_route_policy != 0 && m_source_route_policy != 5 &&
        m_source_route_policy != 6) {
        const size_t pair_count = static_cast<size_t>(router_count) *
            router_count;
        fatal_if(m_source_route_candidate_counts.size() != pair_count ||
                 m_source_route_candidate_latencies.size() !=
                     pair_count * m_source_route_candidates ||
                 m_source_route_candidate_express_counts.size() !=
                     pair_count * m_source_route_candidates ||
                 m_source_route_candidate_express_ids.size() !=
                     pair_count * m_source_route_candidates * 2,
                 "source-route candidate table has invalid dimensions for "
                 "K=%u", m_source_route_candidates);
        const uint32_t n = m_source_route_candidate_counts[pair_index];
        fatal_if(n == 0 || n > m_source_route_candidates,
                 "source-route candidate count %u is invalid for K=%u",
                 n, m_source_route_candidates);
        uint32_t best = m_source_route_policy == 3 ?
            random_mt.random<uint32_t>(0, n - 1) : 0;
        double best_cost = std::numeric_limits<double>::infinity();
        for (uint32_t c = 0; c < n && m_source_route_policy != 3; ++c) {
            const size_t base = pair_index * m_source_route_candidates + c;
            const uint32_t ec = m_source_route_candidate_express_counts[base];
            double cost = m_source_route_candidate_latencies[base];
            for (uint32_t i = 0; i < ec; ++i) {
                const uint32_t id = m_source_route_candidate_express_ids[base * 2 + i];
                if (m_source_route_policy == 4) {
                    const auto [observed_q, observed_r] =
                        observedExpressPressure(
                            route.src_router, id, route.vnet);
                    cost += m_source_route_reservation_weight *
                        observed_r;
                    cost += m_source_route_vc_weight *
                        observed_q;
                }
            }
            if (cost < best_cost) { best_cost = cost; best = c; }
        }
        const bool force_mesh = m_source_route_policy == 4 &&
            m_source_route_candidate_express_counts[
                pair_index * m_source_route_candidates + best] > 0 &&
            !admitExpressRoute(route.src_router, route.dest_router);
        if (force_mesh) {
            // An empty express sequence is the canonical XY-only source
            // route.  Construct it directly: the latency-ranked top-K table
            // is not required to retain an explicit mesh candidate.
            count = 0;
            route.express_ids.clear();
        } else {
            const size_t base = pair_index * m_source_route_candidates + best;
            count = m_source_route_candidate_express_counts[base];
            route.express_ids.clear();
            for (uint32_t i = 0; i < count; ++i) {
                route.express_ids.push_back(
                    m_source_route_candidate_express_ids[base * 2 + i]);
            }
        }
    }
    fatal_if(count > std::numeric_limits<uint8_t>::max(),
             "source-route express count %u exceeds metadata limit", count);
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
    if (m_source_route_reservation_mode == "registered") {
        registerSourceRoute(route);
        return;
    }
    for (uint16_t id : route.express_ids) {
        fatal_if(id >= m_reservation_current.size(),
                 "reservation express id %u is invalid", id);
        ++m_reservation_current[id];
        m_express_reservation_increments[id]++;
    }
}

void
GarnetNetwork::releaseDynamicRoute(
    const RouteInfo &route, uint16_t begin_stage, int current_router,
    bool cancellation)
{
    if (m_source_route_policy != 6 || route.dynamic_route_routers.empty())
        return;
    const int router_count = m_routers.size();
    int current = current_router;
    const uint16_t end = cancellation ? route.dynamic_route_routers.size() :
                                        begin_stage + 1;
    fatal_if(begin_stage >= route.dynamic_route_routers.size() ||
             end > route.dynamic_route_routers.size(),
             "invalid dynamic-route release stage %u/%zu", begin_stage,
             route.dynamic_route_routers.size());
    for (uint16_t stage = begin_stage; stage < end; ++stage) {
        const int next = route.dynamic_route_routers[stage];
        const size_t key = static_cast<size_t>(current) * router_count + next;
        fatal_if(key >= m_directed_link_reservations.size() ||
                 m_directed_link_reservations[key] <= 0,
                 "dynamic-route reservation underflow on %d->%d", current,
                 next);
        --m_directed_link_reservations[key];
        current = next;
    }
}

void
GarnetNetwork::dumpDeadlockState(std::ostream &out)
{
    if (m_deadlock_state_dumped)
        return;
    m_deadlock_state_dumped = true;
    out << "EXPRESS_DEADLOCK_STATE_BEGIN tick=" << curTick() << "\n";
    for (Router *router : m_routers) {
        for (int inport = 0; inport < router->get_num_inports(); ++inport) {
            InputUnit *input = router->getInputUnit(inport);
            for (uint32_t vc = 0; vc < router->get_num_vcs(); ++vc) {
                if (!input->isReady(vc, curTick()))
                    continue;
                flit *head = input->peekTopFlit(vc);
                const RouteInfo route = head->get_route();
                const int outport = input->get_outport(vc);
                out << "EXPRESS_DEADLOCK_VC router=" << router->get_id()
                    << " inport=" << inport
                    << " in_dir=" << input->get_direction()
                    << " vc=" << vc << " outport=" << outport
                    << " out_dir=";
                if (outport >= 0)
                    out << router->getOutportDirection(outport);
                else
                    out << "Unassigned";
                out << " outvc=" << input->get_outvc(vc)
                    << " src=" << route.src_router
                    << " dest=" << route.dest_router
                    << " escape=" << route.escape_vc << "\n";
            }
        }
    }
    out << "EXPRESS_DEADLOCK_STATE_END\n";
}

int
GarnetNetwork::meshDistance(int first, int second) const
{
    return std::abs(first % m_num_cols - second % m_num_cols) +
           std::abs(first / m_num_cols - second / m_num_cols);
}

uint64_t
GarnetNetwork::reservationToken(const RouteInfo &route, uint8_t stage) const
{
    fatal_if(stage >= 0xff,
             "source-route reservation stage exceeds token encoding");
    fatal_if(route.source_route_packet_id >
                 (std::numeric_limits<uint64_t>::max() >> 8),
             "source-route packet ID exceeds reservation token encoding");
    return (route.source_route_packet_id << 8) | stage;
}

void
GarnetNetwork::maybeEraseReservationToken(uint64_t token_id)
{
    const auto item = m_reservation_tokens.find(token_id);
    if (item != m_reservation_tokens.end() && item->second.acknowledged &&
        item->second.state == ReservationState::Done)
        m_reservation_tokens.erase(item);
}

void
GarnetNetwork::registerSourceRoute(const RouteInfo &route)
{
    int current = route.src_router;
    uint64_t setup_delay = 1;
    for (uint8_t stage = 0; stage < route.express_count; ++stage) {
        const int directed_id = route.express_ids.at(stage);
        const int entry = getExpressRouteSource(directed_id);
        setup_delay += meshDistance(current, entry);
        const uint64_t token_id = reservationToken(route, stage);
        ReservationToken token;
        token.source = route.src_router;
        token.directed_id = directed_id;
        fatal_if(!m_reservation_tokens.emplace(token_id, token).second,
                 "duplicate source-route reservation token");
        ++m_local_unacknowledged.at(route.src_router).at(directed_id);
        m_reservation_control.emplace(
            curCycle() + setup_delay,
            ReservationControlEvent{
                ReservationControlKind::Register, token_id});
        ++m_reservation_registration_messages;
        setup_delay += m_express_link_latencies.at(directed_id / 2);
        current = getExpressRouteDestination(directed_id);
    }
}

void
GarnetNetwork::processReservationControl()
{
    if (m_source_route_reservation_mode != "registered")
        return;
    const uint64_t now = curCycle();
    auto item = m_reservation_control.begin();
    while (item != m_reservation_control.end() && item->first <= now) {
        const ReservationControlEvent event = item->second;
        item = m_reservation_control.erase(item);
        const auto found = m_reservation_tokens.find(event.token);
        fatal_if(found == m_reservation_tokens.end(),
                 "reservation control event has no matching token");
        ReservationToken &token = found->second;
        const int directed_id = token.directed_id;

        if (event.kind == ReservationControlKind::Register) {
            if (token.state == ReservationState::Pending) {
                ++m_reservation_current.at(directed_id);
                ++m_express_reservation_increments[directed_id];
                token.state = ReservationState::Registered;
            } else if (token.state == ReservationState::CancelArrived) {
                token.state = ReservationState::Done;
            } else {
                fatal("duplicate or invalid reservation registration");
            }

            // Clear the source-local shadow only when the first periodic
            // advertisement carrying the registration can reach the source.
            // This makes the transition continuous despite delayed gossip.
            const uint64_t period = m_source_route_info_period;
            const uint64_t next_advertisement = now +
                ((period - now % period) % period);
            const int entry = getExpressRouteSource(directed_id);
            m_reservation_control.emplace(
                next_advertisement + m_source_route_info_delay +
                    meshDistance(entry, token.source),
                ReservationControlEvent{
                    ReservationControlKind::Acknowledge, event.token});
        } else if (event.kind == ReservationControlKind::Acknowledge) {
            fatal_if(token.acknowledged,
                     "duplicate reservation acknowledgement");
            uint32_t &pending =
                m_local_unacknowledged.at(token.source).at(directed_id);
            fatal_if(pending == 0,
                     "source-local reservation acknowledgement underflow");
            --pending;
            token.acknowledged = true;
            ++m_reservation_registration_acks;
        } else {
            if (token.state == ReservationState::Pending) {
                token.state = ReservationState::CancelArrived;
            } else if (token.state == ReservationState::Registered) {
                fatal_if(m_reservation_current.at(directed_id) <= 0,
                         "registered reservation underflow on cancellation");
                --m_reservation_current.at(directed_id);
                ++m_express_reservation_decrements[directed_id];
                token.state = ReservationState::Done;
            } else {
                fatal("invalid reservation cancellation state");
            }
            ++m_reservation_registration_cancels;
        }
        maybeEraseReservationToken(event.token);
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
        // The advertised q signal is occupied express-output VCs, not the
        // transient link traversal buffer used by the retired per-hop router.
        // Statistics are not indexed by vnet, so retain the busiest vnet.
        uint64_t q = 0;
        for (int vnet = 0; vnet < m_virtual_networks; ++vnet) {
            q = std::max<uint64_t>(q, static_cast<uint64_t>(
                expressVcOccupancy(i, vnet)));
        }
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
GarnetNetwork::releaseSourceRouteExpress(
    const RouteInfo &route, uint8_t stage, int current_router,
    bool cancellation)
{
    fatal_if(stage >= route.express_count,
             "release reservation stage %u is invalid", stage);
    const int directed_id = route.express_ids.at(stage);
    fatal_if(directed_id < 0 || directed_id >= m_reservation_current.size(),
             "release reservation express id %d is invalid", directed_id);
    if (m_source_route_reservation_mode == "registered") {
        const uint64_t token_id = reservationToken(route, stage);
        const auto found = m_reservation_tokens.find(token_id);
        fatal_if(found == m_reservation_tokens.end(),
                 "express reservation release has no matching token");
        ReservationToken &token = found->second;
        if (cancellation) {
            const int entry = getExpressRouteSource(directed_id);
            m_reservation_control.emplace(
                curCycle() + 1 + meshDistance(current_router, entry),
                ReservationControlEvent{
                    ReservationControlKind::Cancel, token_id});
            return;
        }
        if (token.state != ReservationState::Registered) {
            ++m_reservation_late_registrations;
            fatal("packet reached express link before route setup registered it");
        }
        fatal_if(m_reservation_current[directed_id] <= 0,
                 "registered reservation underflow on express id %d",
                 directed_id);
        --m_reservation_current[directed_id];
        ++m_express_reservation_decrements[directed_id];
        token.state = ReservationState::Done;
        maybeEraseReservationToken(token_id);
        return;
    }
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
    m_reservation_registration_messages
        .name(name() + ".reservation_registration_messages");
    m_reservation_registration_acks
        .name(name() + ".reservation_registration_acks");
    m_reservation_registration_cancels
        .name(name() + ".reservation_registration_cancels");
    m_reservation_late_registrations
        .name(name() + ".reservation_late_registrations");
    m_express_info_queries.name(name() + ".express_info_queries");
    m_express_info_true_q_sum.name(name() + ".express_info_true_q_sum");
    m_express_info_observed_q_sum
        .name(name() + ".express_info_observed_q_sum");
    m_express_info_true_r_sum.name(name() + ".express_info_true_r_sum");
    m_express_info_observed_r_sum
        .name(name() + ".express_info_observed_r_sum");
    m_express_info_local_pending_sum
        .name(name() + ".express_info_local_pending_sum");
    m_express_info_queries_before_periodic_event
        .name(name() + ".express_info_queries_before_periodic_event");
    m_express_info_queries_after_entry_router_wakeup
        .name(name() + ".express_info_queries_after_entry_router_wakeup");
    m_express_info_snapshots_before_periodic_event
        .name(name() + ".express_info_snapshots_before_periodic_event");
    m_express_info_snapshots_before_periodic_by_parity
        .init(2)
        .name(name() + ".express_info_snapshots_before_periodic_by_parity")
        .flags(statistics::oneline);
    m_express_info_snapshot_router_wakeups_sum
        .name(name() + ".express_info_snapshot_router_wakeups_sum");

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
