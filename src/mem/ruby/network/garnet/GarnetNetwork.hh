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


#ifndef __MEM_RUBY_NETWORK_GARNET_0_GARNETNETWORK_HH__
#define __MEM_RUBY_NETWORK_GARNET_0_GARNETNETWORK_HH__

#include <deque>
#include <iostream>
#include <limits>
#include <map>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "mem/ruby/network/Network.hh"
#include "mem/ruby/network/fault_model/FaultModel.hh"
#include "mem/ruby/network/garnet/CommonTypes.hh"
#include "params/GarnetNetwork.hh"

namespace gem5
{

namespace ruby
{

class FaultModel;
class NetDest;

namespace garnet
{

class NetworkInterface;
class Router;
class NetworkLink;
class NetworkBridge;
class CreditLink;

class GarnetNetwork : public Network
{
  public:
    typedef GarnetNetworkParams Params;
    GarnetNetwork(const Params &p);
    ~GarnetNetwork() = default;

    void init();
    void startup() override;

    const char *garnetVersion = "3.0";

    // Configuration (set externally)

    // for 2D topology
    int getNumRows() const { return m_num_rows; }
    int getNumCols() { return m_num_cols; }

    // for network
    uint32_t getNiFlitSize() const { return m_ni_flit_size; }
    uint32_t getBuffersPerDataVC() { return m_buffers_per_data_vc; }
    uint32_t getBuffersPerCtrlVC() { return m_buffers_per_ctrl_vc; }
    int getRoutingAlgorithm() const { return m_routing_algorithm; }
    int getExpressRouteSource(int directed_id) const;
    int getExpressRouteDestination(int directed_id) const;
    bool isSourceRouteEnabled() const { return m_source_route_enabled; }
    void initializeSourceRoute(RouteInfo &route);
    void reserveSourceRoute(const RouteInfo &route);
    void releaseSourceRouteExpress(
        const RouteInfo &route, uint8_t stage, int current_router,
        bool cancellation);
    double expressVcOccupancy(int directed_id, int vnet);
    uint32_t getExpressEscapeTimeout() const
    { return m_express_escape_timeout; }
    bool isExpressEscapeEnabled() const { return m_express_escape_enabled; }
    void incrementExpressLinkTraversal() { m_express_link_traversals++; }
    void incrementEscapeTraversal() { m_escape_vc_traversals++; }
    void recordEscapeTransition(RouteInfo &route, Tick tick);
    void recordSourceRouteSelection(const RouteInfo &route);
    void recordDeliveredSourceRoute(const RouteInfo &route);
    void recordDeliveredEscape(const RouteInfo &route);
    void sampleExpressState();
    void recordRouterWakeup(int router_id);

    bool isFaultModelEnabled() const { return m_enable_fault_model; }
    FaultModel* fault_model;


    // Internal configuration
    bool isVNetOrdered(int vnet) const { return m_ordered[vnet]; }
    VNET_type
    get_vnet_type(int vnet)
    {
        return m_vnet_type[vnet];
    }
    int getNumRouters();
    int get_router_id(int ni, int vnet);


    // Methods used by Topology to setup the network
    void makeExtOutLink(SwitchID src, NodeID dest, BasicLink* link,
                     std::vector<NetDest>& routing_table_entry);
    void makeExtInLink(NodeID src, SwitchID dest, BasicLink* link,
                    std::vector<NetDest>& routing_table_entry);
    void makeInternalLink(SwitchID src, SwitchID dest, BasicLink* link,
                          std::vector<NetDest>& routing_table_entry,
                          PortDirection src_outport_dirn,
                          PortDirection dest_inport_dirn);

    bool functionalRead(Packet *pkt, WriteMask &mask);
    //! Function for performing a functional write. The return value
    //! indicates the number of messages that were written.
    uint32_t functionalWrite(Packet *pkt);

    // Stats
    void collateStats();
    void regStats();
    void resetStats();
    void print(std::ostream& out) const;

    // increment counters
    void increment_injected_packets(int vnet) { m_packets_injected[vnet]++; }
    void increment_received_packets(int vnet) { m_packets_received[vnet]++; }

    void
    increment_packet_network_latency(Tick latency, int vnet)
    {
        m_packet_network_latency[vnet] += latency;
    }

    void
    increment_packet_queueing_latency(Tick latency, int vnet)
    {
        m_packet_queueing_latency[vnet] += latency;
    }

    void increment_injected_flits(int vnet) { m_flits_injected[vnet]++; }
    void increment_received_flits(int vnet) { m_flits_received[vnet]++; }

    void
    increment_flit_network_latency(Tick latency, int vnet)
    {
        m_flit_network_latency[vnet] += latency;
    }

    void
    increment_flit_queueing_latency(Tick latency, int vnet)
    {
        m_flit_queueing_latency[vnet] += latency;
    }

    void
    increment_total_hops(int hops)
    {
        m_total_hops += hops;
    }

    void update_traffic_distribution(RouteInfo route);
    int getNextPacketID() { return m_next_packet_id++; }

  protected:
    // Configuration
    int m_num_rows;
    int m_num_cols;
    uint32_t m_ni_flit_size;
    uint32_t m_max_vcs_per_vnet;
    uint32_t m_buffers_per_ctrl_vc;
    uint32_t m_buffers_per_data_vc;
    int m_routing_algorithm;
    std::vector<uint32_t> m_express_link_endpoints;
    std::vector<uint32_t> m_express_link_latencies;
    bool m_source_route_enabled;
    std::vector<uint32_t> m_source_route_express_counts;
    std::vector<uint32_t> m_source_route_express_ids;
    std::vector<uint32_t> m_source_route_candidate_counts;
    std::vector<uint32_t> m_source_route_candidate_latencies;
    std::vector<uint32_t> m_source_route_candidate_express_counts;
    std::vector<uint32_t> m_source_route_candidate_express_ids;
    uint32_t m_source_route_candidates;
    uint32_t m_source_route_policy;
    double m_source_route_reservation_weight;
    double m_source_route_vc_weight;
    std::string m_source_route_info_mode;
    std::string m_source_route_reservation_mode;
    uint32_t m_source_route_info_period;
    uint32_t m_source_route_info_delay;
    uint32_t m_source_route_info_bits;
    double m_source_route_admission_fraction;
    uint32_t m_express_escape_timeout;
    bool m_express_escape_enabled;
    bool m_enable_fault_model;

    // Statistical variables
    statistics::Vector m_packets_received;
    statistics::Vector m_packets_injected;
    statistics::Vector m_packet_network_latency;
    statistics::Vector m_packet_queueing_latency;

    statistics::Formula m_avg_packet_vnet_latency;
    statistics::Formula m_avg_packet_vqueue_latency;
    statistics::Formula m_avg_packet_network_latency;
    statistics::Formula m_avg_packet_queueing_latency;
    statistics::Formula m_avg_packet_latency;

    statistics::Vector m_flits_received;
    statistics::Vector m_flits_injected;
    statistics::Vector m_flit_network_latency;
    statistics::Vector m_flit_queueing_latency;

    statistics::Formula m_avg_flit_vnet_latency;
    statistics::Formula m_avg_flit_vqueue_latency;
    statistics::Formula m_avg_flit_network_latency;
    statistics::Formula m_avg_flit_queueing_latency;
    statistics::Formula m_avg_flit_latency;

    statistics::Scalar m_total_ext_in_link_utilization;
    statistics::Scalar m_total_ext_out_link_utilization;
    statistics::Scalar m_total_int_link_utilization;
    statistics::Scalar m_average_link_utilization;
    statistics::Vector m_average_vc_load;

    statistics::Scalar  m_total_hops;
    statistics::Scalar m_express_link_traversals;
    statistics::Scalar m_escape_vc_traversals;
    statistics::Scalar m_escape_vc_transitions;
    statistics::Scalar m_escape_transition_delay_ticks;
    statistics::Scalar m_delivered_escape_packets;
    statistics::Vector m_express_reservation_current;
    statistics::Vector m_express_reservation_increments;
    statistics::Vector m_express_reservation_decrements;
    statistics::Vector m_source_route_packets_planned;
    statistics::Vector m_source_route_packets_delivered;
    statistics::Vector m_express_edge_packets_selected;
    statistics::Vector m_express_q_sample_sum;
    statistics::Vector m_express_q_sample_max;
    statistics::Vector m_express_r_sample_sum;
    statistics::Vector m_express_r_sample_max;
    statistics::Scalar m_express_state_sample_count;
    statistics::Scalar m_reservation_registration_messages;
    statistics::Scalar m_reservation_registration_acks;
    statistics::Scalar m_reservation_registration_cancels;
    statistics::Scalar m_reservation_late_registrations;
    statistics::Scalar m_express_info_queries;
    statistics::Scalar m_express_info_true_q_sum;
    statistics::Scalar m_express_info_observed_q_sum;
    statistics::Scalar m_express_info_true_r_sum;
    statistics::Scalar m_express_info_observed_r_sum;
    statistics::Scalar m_express_info_local_pending_sum;
    // Diagnostic counters used to pin down same-cycle ordering between the
    // periodic information event, router credit processing, and NI route
    // selection.  They observe the existing implementation and do not alter
    // routing or control decisions.
    statistics::Scalar m_express_info_queries_before_periodic_event;
    statistics::Scalar m_express_info_queries_after_entry_router_wakeup;
    statistics::Scalar m_express_info_snapshots_before_periodic_event;
    statistics::Vector m_express_info_snapshots_before_periodic_by_parity;
    statistics::Scalar m_express_info_snapshot_router_wakeups_sum;
    statistics::Vector m_int_link_utilization;
    statistics::Formula m_avg_hops;

    std::vector<std::vector<statistics::Scalar *>> m_data_traffic_distribution;
    std::vector<std::vector<statistics::Scalar *>> m_ctrl_traffic_distribution;

  private:
    enum class ReservationControlKind : uint8_t
    {
        Register,
        Acknowledge,
        Cancel
    };

    enum class ReservationState : uint8_t
    {
        Pending,
        Registered,
        CancelArrived,
        Done
    };

    struct ReservationControlEvent
    {
        ReservationControlKind kind;
        uint64_t token;
    };

    struct ReservationToken
    {
        int source = -1;
        int directed_id = -1;
        ReservationState state = ReservationState::Pending;
        bool acknowledged = false;
    };

    struct ExpressInfoSnapshot
    {
        uint64_t cycle;
        std::vector<uint32_t> q;
        std::vector<uint32_t> r;
    };

    uint32_t quantizeExpressPressure(uint64_t value) const;
    void captureExpressInformation();
    void processExpressInfoEvent();
    void processReservationControl();
    void registerSourceRoute(const RouteInfo &route);
    uint64_t reservationToken(const RouteInfo &route, uint8_t stage) const;
    void maybeEraseReservationToken(uint64_t token);
    int meshDistance(int first, int second) const;
    std::pair<double, double> observedExpressPressure(
        int source, int directed_id, int vnet);
    bool admitExpressRoute(int source, int destination) const;

    GarnetNetwork(const GarnetNetwork& obj);
    GarnetNetwork& operator=(const GarnetNetwork& obj);

    std::vector<VNET_type > m_vnet_type;
    std::vector<Router *> m_routers;   // All Routers in Network
    std::vector<NetworkLink *> m_networklinks; // All flit links in the network
    std::vector<NetworkBridge *> m_networkbridges; // All network bridges
    std::vector<CreditLink *> m_creditlinks; // All credit links in the network
    std::vector<NetworkInterface *> m_nis;   // All NI's in Network
    int m_next_packet_id; // static vairable for packet id allocation
    std::vector<int64_t> m_reservation_current;
    std::deque<ExpressInfoSnapshot> m_express_info_history;
    uint64_t m_express_info_last_cycle;
    uint64_t m_express_info_event_last_cycle =
        std::numeric_limits<uint64_t>::max();
    std::vector<uint64_t> m_router_last_wakeup_cycle;
    EventFunctionWrapper m_express_info_event;
    std::multimap<uint64_t, ReservationControlEvent> m_reservation_control;
    std::unordered_map<uint64_t, ReservationToken> m_reservation_tokens;
    std::vector<std::vector<uint32_t>> m_local_unacknowledged;
    std::vector<uint64_t> m_q_sample_sum;
    std::vector<uint64_t> m_q_sample_max;
    std::vector<uint64_t> m_r_sample_sum;
    std::vector<uint64_t> m_r_sample_max;
    uint64_t m_express_state_samples = 0;
};

inline std::ostream&
operator<<(std::ostream& out, const GarnetNetwork& obj)
{
    obj.print(out);
    out << std::flush;
    return out;
}

} // namespace garnet
} // namespace ruby
} // namespace gem5

#endif //__MEM_RUBY_NETWORK_GARNET_0_GARNETNETWORK_HH__
