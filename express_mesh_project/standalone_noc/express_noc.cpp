// A compact cycle-level reproduction of the Garnet subset used by
// express_mesh_project/run_phase3_measurement_v2.py.

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <deque>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <numeric>
#include <queue>
#include <random>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

constexpr int kDefaultRows = 8;
constexpr int kDefaultVcs = 4;
constexpr uint64_t kEmpty = std::numeric_limits<uint64_t>::max();

struct Options {
    std::string topology_file;
    std::string topology = "mesh";
    std::string routing = "deterministic";
    std::string source_mesh_routing = "adaptive";
    std::string traffic = "uniform_random";
    std::string output;
    std::string deadlock_trace;
    double rate = 0.02;
    uint32_t seed = 1;
    uint32_t random_placement_seed = 1;
    uint32_t express_wire_budget = 16;
    uint32_t express_max_degree = 1;
    uint32_t express_min_wire_length = 3;
    uint32_t vcs_per_vnet = kDefaultVcs;
    uint32_t buffer_depth = 1;
    uint32_t packet_flits = 1;
    uint32_t router_latency = 1;
    uint32_t mesh_link_latency = 1;
    std::string express_latency_mode = "topology";
    uint32_t express_latency = 1;
    uint32_t express_wire_per_cycle = 4;
    uint32_t injection_period = 2;
    int dimension = 0;
    int node_count = 0;
    uint64_t warmup = 20000;
    uint64_t measurement = 100000;
    uint64_t deadlock_threshold = 50000;
    uint64_t drain_cycles = 0;
    bool source_route = false;
    uint32_t source_route_policy = 0;
    uint32_t source_route_candidates = 8;
    bool retain_mesh_candidate = false;
    double reservation_weight = 0.5;
    double express_vc_weight = 1.0;
    double express_waiter_weight = 0.0;
    std::string express_info_mode = "instant";
    uint64_t express_info_period = 1;
    uint64_t express_info_delay = 0;
    uint32_t express_info_bits = 0;
    double express_admission_fraction = 1.0;
    std::string express_reservation_mode = "instant";
    bool no_escape = false;
    bool correct_no_escape_vcs = false;
    bool continue_after_ni_watchdog = false;
    double adaptive_threshold = 0.25;
    double adaptive_lambda = 1.0;
    double detour_ratio = 1.5;
    uint64_t escape_timeout = 32;
    int debug_stall_source = -1;
    uint64_t debug_stall_threshold = 1000;
    uint64_t debug_stall_interval = 5000;
    bool self_test = false;
};

[[noreturn]] void fail(const std::string &message) {
    throw std::runtime_error(message);
}

std::string slurp(const std::string &path) {
    std::ifstream input(path);
    if (!input) fail("cannot open topology file: " + path);
    return {std::istreambuf_iterator<char>(input),
            std::istreambuf_iterator<char>()};
}

uint64_t parse_u64(const std::string &s, const char *name) {
    size_t used = 0;
    const auto value = std::stoull(s, &used);
    if (used != s.size()) fail(std::string("invalid ") + name + ": " + s);
    return value;
}

uint32_t parse_u32(const std::string &s, const char *name) {
    const auto value = parse_u64(s, name);
    if (value > std::numeric_limits<uint32_t>::max())
        fail(std::string(name) + " is too large");
    return static_cast<uint32_t>(value);
}

double parse_double(const std::string &s, const char *name) {
    size_t used = 0;
    const double value = std::stod(s, &used);
    if (used != s.size() || !std::isfinite(value))
        fail(std::string("invalid ") + name + ": " + s);
    return value;
}

Options parse_options(int argc, char **argv) {
    Options o;
    auto value = [&](int &i, const char *name) -> std::string {
        if (++i >= argc) fail(std::string("missing value after ") + name);
        return argv[i];
    };
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--topology-file") o.topology_file = value(i, argv[i]);
        else if (a == "--topology") o.topology = value(i, argv[i]);
        else if (a == "--routing") o.routing = value(i, argv[i]);
        else if (a == "--source-mesh-routing") o.source_mesh_routing = value(i, argv[i]);
        else if (a == "--traffic") o.traffic = value(i, argv[i]);
        else if (a == "--output") o.output = value(i, argv[i]);
        else if (a == "--deadlock-trace") o.deadlock_trace = value(i, argv[i]);
        else if (a == "--rate") o.rate = parse_double(value(i, argv[i]), "rate");
        else if (a == "--seed") o.seed = parse_u32(value(i, argv[i]), "seed");
        else if (a == "--random-placement-seed") o.random_placement_seed = parse_u32(value(i, argv[i]), "random placement seed");
        else if (a == "--express-wire-budget") o.express_wire_budget = parse_u32(value(i, argv[i]), "express wire budget");
        else if (a == "--express-max-degree") o.express_max_degree = parse_u32(value(i, argv[i]), "express max degree");
        else if (a == "--express-min-wire-length") o.express_min_wire_length = parse_u32(value(i, argv[i]), "express min wire length");
        else if (a == "--vcs-per-vnet") o.vcs_per_vnet = parse_u32(value(i, argv[i]), "VCs per vnet");
        else if (a == "--buffer-depth") o.buffer_depth = parse_u32(value(i, argv[i]), "buffer depth");
        else if (a == "--packet-flits") o.packet_flits = parse_u32(value(i, argv[i]), "packet flits");
        else if (a == "--router-latency") o.router_latency = parse_u32(value(i, argv[i]), "router latency");
        else if (a == "--mesh-link-latency") o.mesh_link_latency = parse_u32(value(i, argv[i]), "mesh link latency");
        else if (a == "--express-latency-mode") o.express_latency_mode = value(i, argv[i]);
        else if (a == "--express-latency") o.express_latency = parse_u32(value(i, argv[i]), "express latency");
        else if (a == "--express-wire-per-cycle") o.express_wire_per_cycle = parse_u32(value(i, argv[i]), "express wire per cycle");
        else if (a == "--injection-period") o.injection_period = parse_u32(value(i, argv[i]), "injection period");
        else if (a == "--warmup-cycles") o.warmup = parse_u64(value(i, argv[i]), "warmup cycles");
        else if (a == "--measurement-cycles") o.measurement = parse_u64(value(i, argv[i]), "measurement cycles");
        else if (a == "--deadlock-threshold") o.deadlock_threshold = parse_u64(value(i, argv[i]), "deadlock threshold");
        else if (a == "--drain-cycles") o.drain_cycles = parse_u64(value(i, argv[i]), "drain cycles");
        else if (a == "--source-route-policy") o.source_route_policy = parse_u32(value(i, argv[i]), "source route policy");
        else if (a == "--source-route-candidates") o.source_route_candidates = parse_u32(value(i, argv[i]), "source route candidates");
        else if (a == "--reservation-weight") o.reservation_weight = parse_double(value(i, argv[i]), "reservation weight");
        else if (a == "--express-vc-weight") o.express_vc_weight = parse_double(value(i, argv[i]), "express VC weight");
        else if (a == "--express-waiter-weight") o.express_waiter_weight = parse_double(value(i, argv[i]), "express waiter weight");
        else if (a == "--express-info-mode") o.express_info_mode = value(i, argv[i]);
        else if (a == "--express-info-period") o.express_info_period = parse_u64(value(i, argv[i]), "express info period");
        else if (a == "--express-info-delay") o.express_info_delay = parse_u64(value(i, argv[i]), "express info delay");
        else if (a == "--express-info-bits") o.express_info_bits = parse_u32(value(i, argv[i]), "express info bits");
        else if (a == "--express-admission-fraction") o.express_admission_fraction = parse_double(value(i, argv[i]), "express admission fraction");
        else if (a == "--express-reservation-mode") o.express_reservation_mode = value(i, argv[i]);
        else if (a == "--adaptive-threshold") o.adaptive_threshold = parse_double(value(i, argv[i]), "adaptive threshold");
        else if (a == "--adaptive-lambda") o.adaptive_lambda = parse_double(value(i, argv[i]), "adaptive lambda");
        else if (a == "--detour-ratio") o.detour_ratio = parse_double(value(i, argv[i]), "detour ratio");
        else if (a == "--escape-timeout") o.escape_timeout = parse_u64(value(i, argv[i]), "escape timeout");
        else if (a == "--debug-stall-source") o.debug_stall_source = std::stoi(value(i, argv[i]));
        else if (a == "--debug-stall-threshold") o.debug_stall_threshold = parse_u64(value(i, argv[i]), "debug stall threshold");
        else if (a == "--debug-stall-interval") o.debug_stall_interval = parse_u64(value(i, argv[i]), "debug stall interval");
        else if (a == "--source-route") o.source_route = true;
        else if (a == "--retain-mesh-candidate") o.retain_mesh_candidate = true;
        else if (a == "--no-escape") o.no_escape = true;
        else if (a == "--correct-no-escape-vcs") o.correct_no_escape_vcs = true;
        else if (a == "--continue-after-ni-watchdog") o.continue_after_ni_watchdog = true;
        else if (a == "--self-test") o.self_test = true;
        else if (a == "--help" || a == "-h") {
            std::cout <<
                "Usage: express_noc --topology-file FILE [options]\n"
                "  --topology NAME --routing deterministic|adaptive\n"
                "  --source-mesh-routing adaptive|dor_adaptive|monotonic_xy|odd_even|phase_xy|west_first|xy\n"
                "  --traffic uniform_random|cutstress|cutstress_bidirectional|hotspot|bit_complement|tornado|soc_heterogeneous\n"
                "  --rate R --seed S\n"
                "  --warmup-cycles N --measurement-cycles N\n"
                "  --source-route --source-route-policy 0|1|2|3|4|5|6 --no-escape\n"
                "    5: dynamic Dijkstra with express-only pressure\n"
                "    6: ideal dynamic Dijkstra with all-link pressure\n"
                "  --source-route-candidates K\n"
                "  --retain-mesh-candidate (reserve one top-K slot for pure mesh)\n"
                "  --express-wire-budget B --express-max-degree D\n"
                "  --express-min-wire-length L\n"
                "  --vcs-per-vnet N --buffer-depth N --packet-flits N\n"
                "  --router-latency N --mesh-link-latency N\n"
                "  --express-latency-mode topology|fixed|length-aware\n"
                "  --express-latency N --express-wire-per-cycle N\n"
                "  --injection-period N\n"
                "  --reservation-weight W --express-vc-weight W\n"
                "  --express-waiter-weight W\n"
                "  --express-info-mode instant|delayed-global|distance-gossip\n"
                "  --express-info-period N --express-info-delay N\n"
                "  --express-info-bits 0|1|2|3|4\n"
                "  --express-admission-fraction F\n"
                "  --express-reservation-mode instant|registered\n"
                "  --correct-no-escape-vcs --output FILE\n"
                "  --continue-after-ni-watchdog --drain-cycles N\n";
            std::cout <<
                "  --deadlock-trace FILE (write wait-for graph on global stall)\n";
            std::exit(0);
        } else fail("unknown option: " + a);
    }
    if (o.self_test) return o;
    if (o.topology_file.empty()) fail("--topology-file is required");
    if (o.rate < 0.0 || o.rate > 1.0) fail("rate must be in [0,1]");
    if (o.measurement == 0) fail("measurement cycles must be positive");
    if (o.source_route_policy > 6) fail("source route policy must be 0..6");
    if (o.source_route_candidates == 0 || o.source_route_candidates > 512)
        fail("source route candidates must be in 1..512");
    if (o.vcs_per_vnet < 2 || o.vcs_per_vnet > 64)
        fail("VCs per vnet must be in 2..64");
    if (o.buffer_depth == 0 || o.packet_flits == 0 ||
        o.router_latency == 0 || o.mesh_link_latency == 0 ||
        o.express_latency == 0 || o.express_wire_per_cycle == 0 ||
        o.injection_period == 0)
        fail("buffer/flit/latency/injection parameters must be positive");
    if (o.express_latency_mode != "topology" &&
        o.express_latency_mode != "fixed" &&
        o.express_latency_mode != "length-aware")
        fail("express latency mode must be topology, fixed, or length-aware");
    if (o.debug_stall_threshold == 0 || o.debug_stall_interval == 0)
        fail("debug stall threshold and interval must be positive");
    if (o.reservation_weight < 0.0 || o.express_vc_weight < 0.0 ||
        o.express_waiter_weight < 0.0)
        fail("source-route pressure weights must be non-negative");
    if (o.express_info_mode != "instant" &&
        o.express_info_mode != "delayed-global" &&
        o.express_info_mode != "distance-gossip")
        fail("express info mode must be instant, delayed-global, or distance-gossip");
    if (o.express_info_period == 0)
        fail("express info period must be positive");
    if (o.express_info_bits > 4)
        fail("express info bits must be in 0..4");
    if (o.express_admission_fraction < 0.0 ||
        o.express_admission_fraction > 1.0)
        fail("express admission fraction must be in [0,1]");
    if (o.express_reservation_mode != "instant" &&
        o.express_reservation_mode != "registered")
        fail("express reservation mode must be instant or registered");
    if (o.express_reservation_mode == "registered" &&
        (!o.source_route || o.source_route_policy != 4 ||
         o.express_info_mode != "distance-gossip" ||
         o.express_info_delay == 0))
        fail("registered reservations require source-route policy 4 and "
             "distance-gossip with at least one cycle of base delay");
    if (o.routing != "deterministic" && o.routing != "adaptive")
        fail("routing must be deterministic or adaptive");
    if (o.source_mesh_routing != "adaptive" &&
        o.source_mesh_routing != "dor_adaptive" &&
        o.source_mesh_routing != "monotonic_xy" &&
        o.source_mesh_routing != "odd_even" &&
        o.source_mesh_routing != "phase_xy" &&
        o.source_mesh_routing != "west_first" && o.source_mesh_routing != "xy")
        fail("source mesh routing must be adaptive, dor_adaptive, monotonic_xy, odd_even, phase_xy, west_first, or xy");
    if (o.traffic != "uniform_random" && o.traffic != "cutstress" &&
        o.traffic != "cutstress_bidirectional" &&
        o.traffic != "hotspot" && o.traffic != "bit_complement" &&
        o.traffic != "tornado" && o.traffic != "soc_heterogeneous")
        fail("unsupported traffic: " + o.traffic);
    return o;
}

struct ExpressEdge { int u, v, latency, wire; };

std::vector<ExpressEdge> load_edges(const std::string &path,Options &o) {
    const std::string text = slurp(path);
    std::smatch dimension;
    if (!std::regex_search(text, dimension,
            std::regex(R"("dimension"\s*:\s*(\d+))")))
        fail("topology has no dimension");
    o.dimension = std::stoi(dimension[1]);
    if (o.dimension < 2 || o.dimension > 64)
        fail("topology dimension must be in 2..64");
    std::smatch node_count;
    if (!std::regex_search(text, node_count,
            std::regex(R"("node_count"\s*:\s*(\d+))")))
        fail("topology has no node_count");
    o.node_count = std::stoi(node_count[1]);
    if (o.node_count != o.dimension * o.dimension)
        fail("topology node_count must equal dimension squared");
    if (o.debug_stall_source < -1 || o.debug_stall_source >= o.node_count)
        fail("debug stall source must be -1 or a valid router id");
    if (o.express_max_degree > uint32_t(o.node_count-1))
        fail("express max degree is too large");
    std::vector<ExpressEdge> result;
    std::vector<int> degree(o.node_count,0);
    std::vector<std::vector<bool>> seen(
        o.node_count,std::vector<bool>(o.node_count,false));
    int wire_cost=0;
    // Search only the express_links array.  SA output can contain tens of KB
    // of nested search metadata; applying libstdc++'s backtracking object
    // regex to the whole document can recurse until stack overflow.
    const size_t key=text.find("\"express_links\"");
    if(key==std::string::npos)fail("topology has no express_links array");
    const size_t array_begin=text.find('[',key);
    if(array_begin==std::string::npos)fail("malformed express_links array");
    size_t array_end=std::string::npos;
    int bracket_depth=0;bool in_string=false,escaped=false;
    for(size_t index=array_begin;index<text.size();++index) {
        const char value=text[index];
        if(in_string) {
            if(escaped)escaped=false;
            else if(value=='\\')escaped=true;
            else if(value=='\"')in_string=false;
            continue;
        }
        if(value=='\"'){in_string=true;continue;}
        if(value=='[')++bracket_depth;
        else if(value==']' && --bracket_depth==0) {
            array_end=index+1;break;
        }
    }
    if(array_end==std::string::npos)fail("unterminated express_links array");
    const std::string link_text=text.substr(array_begin,array_end-array_begin);
    const std::regex object(R"(\{[^\{\}]*\})");
    const std::regex u_re(R"("u"\s*:\s*(\d+))");
    const std::regex v_re(R"("v"\s*:\s*(\d+))");
    const std::regex l_re(R"("latency"\s*:\s*(\d+))");
    const std::regex w_re(R"("wire_length"\s*:\s*(\d+))");
    for (auto it = std::sregex_iterator(link_text.begin(), link_text.end(), object);
         it != std::sregex_iterator(); ++it) {
        const std::string record = it->str();
        std::smatch um, vm, lm, wm;
        if (!std::regex_search(record, um, u_re) ||
            !std::regex_search(record, vm, v_re) ||
            !std::regex_search(record, lm, l_re) ||
            !std::regex_search(record, wm, w_re)) continue;
        ExpressEdge edge{std::stoi(um[1]), std::stoi(vm[1]),
                         std::stoi(lm[1]), std::stoi(wm[1])};
        if (edge.u < 0 || edge.u >= o.node_count || edge.v < 0 ||
            edge.v >= o.node_count || edge.u == edge.v || edge.latency < 1)
            fail("invalid express edge in " + path);
        const int ux = edge.u % o.dimension, uy = edge.u / o.dimension;
        const int vx = edge.v % o.dimension, vy = edge.v / o.dimension;
        if (std::abs(ux-vx) + std::abs(uy-vy) != edge.wire)
            fail("express wire_length does not match endpoints");
        if(o.express_latency_mode=="fixed")
            edge.latency=int(o.express_latency);
        else if(o.express_latency_mode=="length-aware")
            edge.latency=std::max(1,int((edge.wire+
                int(o.express_wire_per_cycle)-1)/int(o.express_wire_per_cycle)));
        if(edge.wire<int(o.express_min_wire_length))
            fail("express edge violates configured minimum wire length");
        if(seen[edge.u][edge.v])fail("duplicate express edge");
        seen[edge.u][edge.v]=seen[edge.v][edge.u]=true;
        if(++degree[edge.u]>int(o.express_max_degree) ||
           ++degree[edge.v]>int(o.express_max_degree))
            fail("express edge violates configured max degree");
        wire_cost+=edge.wire;
        if(wire_cost>int(o.express_wire_budget))
            fail("express edge set violates configured wire budget");
        result.push_back(edge);
    }
    return result;
}

struct DirectedLink {
    int src = -1, dst = -1, latency = 1;
    int outport = -1, inport = -1;
    int express_id = -1;
    std::vector<bool> busy;
    uint64_t activity = 0;
    uint64_t next_send_cycle = 0;
    std::deque<uint64_t> output_ready_cycles;
};

struct Candidate {
    int latency = 0;
    std::vector<int> express_ids;
};

struct Pending { int dest; uint64_t created; };

struct Packet {
    uint64_t id = 0;
    int src = 0, dest = 0;
    uint64_t created = 0, injected = 0;
    bool source_routed = false, escape = false;
    std::vector<int> express_ids;
    size_t express_stage = 0;
    // Policies 5/6 commit the exact directed-link sequence selected by a
    // per-packet Dijkstra. Existing candidate policies leave this empty.
    std::vector<int> planned_links;
    size_t planned_link_stage = 0;
    uint8_t express_traversed = 0;
    // -1 until the corresponding mesh segment commits to a dimension order;
    // 0 is XY and 1 is YX.  Two express hops create at most three segments.
    std::array<int8_t,3> segment_yx{{-1,-1,-1}};
    std::array<int8_t,3> phase_vc{{0,1,2}};
    std::array<int8_t,3> phase_vc_low{{0,1,2}};
    std::array<int8_t,3> phase_vc_high{{0,1,2}};
    int hops = 0;
};

struct InputSlot {
    uint64_t packet = kEmpty;
    uint64_t enqueue = 0;
    int outport = -1; // 0 is Local, link index + 1 is an internal output.
};

struct InputPort {
    std::vector<InputSlot> slots;
    int rr_vc = 0;
    explicit InputPort(size_t vcs=0) : slots(vcs) {}
};

struct Router {
    std::vector<InputPort> inputs;
    // -1 for Local; otherwise the directed link feeding this input port.
    std::vector<int> input_upstream;
    std::vector<int> outgoing;
    std::vector<bool> local_busy;
    int rr_local_in = 0;
    std::vector<int> rr_link_in;
};

struct Ni {
    std::deque<Pending> messages;
    std::vector<uint64_t> slots;
    std::vector<bool> busy;
    int allocator = 0;
    int rr = 0;
    uint64_t busy_counter = 0;
    uint64_t next_send_cycle = 0;
    explicit Ni(size_t vcs=0) : slots(vcs,kEmpty), busy(vcs,false) {}
};

uint64_t update_busy_streak(uint64_t current,bool allocation_blocked) {
    return allocation_blocked ? current+1 : 0;
}

struct Arrival {
    enum Kind { RouterFlit, Ejection } kind = RouterFlit;
    int router = -1, inport = -1, vc = -1;
    uint64_t packet = kEmpty;
};

struct Credit {
    enum Kind { NiVc, LinkVc, LocalVc } kind = NiVc;
    int owner = -1, vc = -1;
};

struct ExpressInfoSnapshot {
    uint64_t cycle = 0;
    std::vector<uint32_t> q, r;
};

struct ReservationControlEvent {
    enum Kind { Register, Acknowledge, Cancel } kind = Register;
    uint64_t token = 0;
};

struct ReservationToken {
    enum State { Pending, Registered, CancelArrived, Done } state = Pending;
    int source = -1, directed_id = -1;
    bool acknowledged = false;
};

struct Stats {
    uint64_t attempts=0, offers=0, generated=0, blocked=0;
    uint64_t injected=0, received=0, initial_retries=0;
    long double latency_sum=0, hops_sum=0;
    uint64_t express_traversals=0, escape_traversals=0;
    uint64_t escape_transitions=0, escape_delay=0, delivered_escape=0;
    uint64_t nonminimal=0;
    std::vector<uint64_t> reservation_inc, reservation_dec;
    std::array<uint64_t,3> planned{}, delivered{};
    std::vector<uint64_t> edge_selected, q_sum, q_max, r_sum, r_max;
    uint64_t state_samples=0;
    uint64_t info_queries=0, info_unknown_queries=0, info_updates=0;
    long double info_q_abs_error=0, info_r_abs_error=0;
    long double info_true_q_sum=0, info_observed_q_sum=0;
    long double info_true_r_sum=0, info_observed_r_sum=0;
    long double info_local_pending_sum=0;
    uint64_t registration_messages=0, registration_acks=0;
    uint64_t registration_cancels=0, late_registrations=0;
    std::vector<uint64_t> per_source_received;
    std::vector<long double> per_source_latency_sum;
    std::array<uint64_t,32> latency_histogram{};
    std::array<uint64_t,32> escape_latency_histogram{};
    std::array<uint64_t,32> adaptive_latency_histogram{};
};

class Simulator {
  public:
    explicit Simulator(Options options)
        : o(std::move(options)), edges(load_edges(o.topology_file,o)), rng(o.seed) {
        node_count=o.node_count;
        routers.resize(node_count);
        for(auto &router:routers)
            router.local_busy.assign(o.vcs_per_vnet,false);
        nis.reserve(node_count);
        for(int i=0;i<node_count;++i)nis.emplace_back(o.vcs_per_vnet);
        link_index.assign(node_count,std::vector<int>(node_count,-1));
        edge_latency.assign(node_count,std::vector<uint32_t>(node_count,0));
        distance.assign(node_count,std::vector<uint32_t>(node_count,0));
        next_hop.assign(node_count,std::vector<int>(node_count,-1));
        neighbors.resize(node_count);
        candidates.assign(node_count,
            std::vector<std::vector<Candidate>>(node_count));
        debug_consecutive_ni_busy.assign(node_count,0);
        build_graph();
        verify_escape_cdg();
        build_candidates();
        const size_t n = 2 * edges.size();
        reservations.assign(n, 0);
        link_reservations.assign(links.size(), 0);
        originated_q.assign(n, 0);
        originated_r.assign(n, 0);
        originated_valid.assign(n, 0);
        local_unacknowledged.assign(
            node_count, std::vector<uint32_t>(n, 0));
        reset_stats();
        size_t max_latency = 1;
        for (const auto &link : links)
            max_latency = std::max(max_latency, size_t(link.latency));
        // Events have bounded delay.  A circular calendar avoids retaining
        // every processed event for a 100k-cycle saturated experiment (the
        // previous absolute-cycle vectors could consume hundreds of MB per
        // worker and trigger WSL OOMs).
        arrivals.resize(max_latency + o.packet_flits + 8);
        credits.resize(o.packet_flits + 8);
        reservation_control.resize(
            4*o.dimension + 2*max_latency + o.express_info_delay + 32);
    }

    void run() {
        const uint64_t injection_limit = o.warmup + o.measurement;
        const uint64_t limit = injection_limit + o.drain_cycles;
        end_cycle = limit;
        for (cycle = 0; cycle < limit; ++cycle) {
            if (cycle == o.warmup) reset_stats();
            // In gem5's 1 GHz tester / 2 GHz Ruby schedule, tester/NI events
            // already queued for an injection tick run before Garnet's
            // default-priority periodic advertisement event.  A source-route
            // query on those ticks lazily captures q/r before the periodic
            // event applies due registration controls; on intervening Ruby
            // ticks the periodic event applies controls first.  Reproduce
            // that measured ordering without changing either q/r policy or
            // the registration protocol itself.
            if (cycle % o.injection_period == 0) {
                update_express_information();
                process_reservation_control();
            } else {
                process_reservation_control();
                update_express_information();
            }
            if (cycle < injection_limit) generate_traffic();
            process_credits();
            process_arrivals();
            route_and_arbitrate();
            run_network_interfaces();
            consume_express_output_queues();
            detect_global_no_progress();
            if (global_no_progress) { end_cycle = cycle; break; }
            if (ni_watchdog_triggered && !o.continue_after_ni_watchdog) {
                stopped_at_ni_watchdog = true;
                end_cycle = cycle;
                break;
            }
            if (o.drain_cycles && cycle >= injection_limit && data_empty()) {
                drain_completed = true;
                drain_completion_cycle = cycle;
                end_cycle = cycle + 1;
                break;
            }
        }
    }

    void write_result(std::ostream &out) const {
        const double denom = double(node_count) * double(o.measurement);
        std::vector<double> util;
        util.reserve(links.size());
        for (const auto &link : links)
            util.push_back(double(link.activity) / double(o.measurement));
        const double mean = util.empty() ? 0.0 :
            std::accumulate(util.begin(), util.end(), 0.0) / util.size();
        double variance = 0;
        for (double x : util) variance += (x-mean)*(x-mean);
        if (!util.empty()) variance /= util.size();
        auto ordered = util;
        std::sort(ordered.begin(), ordered.end());
        const double p95 = ordered.empty() ? 0.0 :
            ordered[size_t(0.95 * double(ordered.size()-1))];
        const auto vector_json = [](const auto &v) {
            std::ostringstream s; s << '[';
            for (size_t i=0; i<v.size(); ++i) {
                if (i) s << ',';
                s << v[i];
            }
            s << ']'; return s.str();
        };
        std::vector<uint64_t> reservation_current;
        reservation_current.reserve(reservations.size());
        for (auto x : reservations) reservation_current.push_back(uint64_t(std::max<int64_t>(0,x)));
        const auto sumv = [](const auto &v) {
            return std::accumulate(v.begin(), v.end(), uint64_t(0));
        };
        const uint64_t res_inc = sumv(stats.reservation_inc);
        const uint64_t res_dec = sumv(stats.reservation_dec);
        const uint64_t res_cur = sumv(reservation_current);
        const uint64_t res_max = reservation_current.empty() ? 0 :
            *std::max_element(reservation_current.begin(), reservation_current.end());
        std::vector<double> q_average(stats.q_sum.size()),r_average(stats.r_sum.size());
        if(stats.state_samples)for(size_t i=0;i<q_average.size();++i){
            q_average[i]=double(stats.q_sum[i])/stats.state_samples;
            r_average[i]=double(stats.r_sum[i])/stats.state_samples;
        }
        std::vector<double> source_throughput(node_count,0.0);
        std::vector<double> source_latency(node_count,0.0);
        double source_sum=0.0,source_square_sum=0.0;
        for(int source=0;source<node_count;++source) {
            source_throughput[source]=double(stats.per_source_received[source])/
                double(o.measurement);
            if(stats.per_source_received[source])
                source_latency[source]=double(
                    stats.per_source_latency_sum[source]/
                    stats.per_source_received[source]);
            source_sum+=source_throughput[source];
            source_square_sum+=source_throughput[source]*
                source_throughput[source];
        }
        const double source_jain=source_square_sum?
            source_sum*source_sum/(node_count*source_square_sum):0.0;
        std::vector<int> link_sources,link_destinations,link_express_ids;
        link_sources.reserve(links.size());link_destinations.reserve(links.size());
        link_express_ids.reserve(links.size());
        for(const auto &link:links) {
            link_sources.push_back(link.src);
            link_destinations.push_back(link.dst);
            link_express_ids.push_back(link.express_id);
        }
        std::array<uint64_t,32> latency_bucket_upper{};
        latency_bucket_upper[0]=1;
        for(size_t i=1;i<latency_bucket_upper.size();++i)
            latency_bucket_upper[i]=uint64_t(1)<<i;
        out << std::setprecision(12) << "{\n"
            << "  \"result_schema_version\": 5,\n"
            << "  \"engine\": \"standalone_express_noc\",\n"
            << "  \"standalone_parameter_schema\": 2,\n"
            << "  \"dimension\": " << o.dimension << ",\n"
            << "  \"node_count\": " << node_count << ",\n"
            << "  \"vcs_per_vnet\": " << o.vcs_per_vnet << ",\n"
            << "  \"buffer_depth_flits\": " << o.buffer_depth << ",\n"
            << "  \"packet_flits\": " << o.packet_flits << ",\n"
            << "  \"router_latency\": " << o.router_latency << ",\n"
            << "  \"mesh_link_latency\": " << o.mesh_link_latency << ",\n"
            << "  \"express_latency_mode\": \"" << o.express_latency_mode << "\",\n"
            << "  \"express_fixed_latency\": " << o.express_latency << ",\n"
            << "  \"express_wire_per_cycle\": " << o.express_wire_per_cycle << ",\n"
            << "  \"injection_period\": " << o.injection_period << ",\n"
            << "  \"source_route\": " << (o.source_route?"true":"false") << ",\n"
            << "  \"source_route_policy\": " << o.source_route_policy << ",\n"
            << "  \"source_route_candidates\": " << o.source_route_candidates << ",\n"
            << "  \"retain_mesh_candidate\": "
            << (o.retain_mesh_candidate?"true":"false") << ",\n"
            << "  \"reservation_weight\": " << o.reservation_weight << ",\n"
            << "  \"express_vc_weight\": " << o.express_vc_weight << ",\n"
            << "  \"express_waiter_weight\": " << o.express_waiter_weight << ",\n"
            << "  \"express_info_mode\": \"" << o.express_info_mode << "\",\n"
            << "  \"express_info_period\": " << o.express_info_period << ",\n"
            << "  \"express_info_delay\": " << o.express_info_delay << ",\n"
            << "  \"express_info_bits\": " << o.express_info_bits << ",\n"
            << "  \"express_admission_fraction\": " << o.express_admission_fraction << ",\n"
            << "  \"express_reservation_mode\": \"" << o.express_reservation_mode << "\",\n"
            << "  \"escape_enabled\": " << (!o.no_escape?"true":"false") << ",\n"
            << "  \"escape_cdg_acyclic\": true,\n"
            << "  \"legacy_no_escape_vc_bug\": " << ((!o.correct_no_escape_vcs)?"true":"false") << ",\n"
            << "  \"no_progress\": " << ((ni_watchdog_triggered||global_no_progress)?"true":"false") << ",\n"
            << "  \"no_progress_cycle\": " << ((ni_watchdog_triggered||global_no_progress)?std::to_string(ni_watchdog_triggered?ni_watchdog_cycle:global_no_progress_cycle):"null") << ",\n"
            << "  \"simulation_end_tick\": " << end_cycle*500 << ",\n"
            << "  \"termination_reason\": \"" << termination_reason() << "\",\n"
            << "  \"topology\": \"" << o.topology << "\",\n"
            << "  \"random_placement_seed\": " << o.random_placement_seed << ",\n"
            << "  \"express_wire_budget\": " << o.express_wire_budget << ",\n"
            << "  \"express_max_degree\": " << o.express_max_degree << ",\n"
            << "  \"express_min_wire_length\": " << o.express_min_wire_length << ",\n"
            << "  \"routing\": \"" << o.routing << "\",\n"
            << "  \"source_mesh_routing\": \"" << o.source_mesh_routing << "\",\n"
            << "  \"traffic\": \"" << o.traffic << "\",\n"
            << "  \"configured_injection_rate\": " << o.rate << ",\n"
            << "  \"seed\": " << o.seed << ",\n"
            << "  \"warmup_cycles\": " << o.warmup << ",\n"
            << "  \"measurement_cycles\": " << o.measurement << ",\n"
            << "  \"garnet_deadlock_threshold\": " << o.deadlock_threshold << ",\n"
            << "  \"drain_cycles\": " << o.drain_cycles << ",\n"
            << "  \"continue_after_ni_watchdog\": " << (o.continue_after_ni_watchdog?"true":"false") << ",\n"
            << "  \"ni_watchdog_triggered\": " << (ni_watchdog_triggered?"true":"false") << ",\n"
            << "  \"ni_watchdog_cycle\": " << (ni_watchdog_triggered?std::to_string(ni_watchdog_cycle):"null") << ",\n"
            << "  \"ni_watchdog_source\": " << (ni_watchdog_triggered?std::to_string(ni_watchdog_source):"null") << ",\n"
            << "  \"watchdog_cycles_since_flit_move\": " << (ni_watchdog_triggered?std::to_string(watchdog_move_age):"null") << ",\n"
            << "  \"watchdog_cycles_since_delivery\": " << (ni_watchdog_triggered&&had_delivery_at_watchdog?std::to_string(watchdog_delivery_age):"null") << ",\n"
            << "  \"watchdog_lifetime_flit_moves\": " << watchdog_lifetime_moves << ",\n"
            << "  \"watchdog_lifetime_deliveries\": " << watchdog_lifetime_deliveries << ",\n"
            << "  \"max_ni_busy_streak\": " << max_ni_busy_streak << ",\n"
            << "  \"max_ni_busy_streak_source\": " << max_ni_busy_streak_source << ",\n"
            << "  \"max_ni_busy_streak_cycle\": " << max_ni_busy_streak_cycle << ",\n"
            << "  \"max_ni_busy_streak_before_drain\": " << max_ni_busy_streak_before_drain << ",\n"
            << "  \"global_no_progress_detected\": " << (global_no_progress?"true":"false") << ",\n"
            << "  \"global_no_progress_cycle\": " << (global_no_progress?std::to_string(global_no_progress_cycle):"null") << ",\n"
            << "  \"lifetime_flit_moves\": " << lifetime_moves << ",\n"
            << "  \"lifetime_deliveries\": " << lifetime_deliveries << ",\n"
            << "  \"drain_completed\": " << (drain_completed?"true":"false") << ",\n"
            << "  \"drain_completion_cycle\": " << (drain_completed?std::to_string(drain_completion_cycle):"null") << ",\n"
            << "  \"network_data_empty\": " << (data_empty()?"true":"false") << ",\n"
            << "  \"pending_source_messages\": " << pending_source_messages() << ",\n"
            << "  \"live_network_packets\": " << packets.size() << ",\n"
            << "  \"express_adaptive_threshold\": " << o.adaptive_threshold << ",\n"
            << "  \"express_adaptive_lambda\": " << o.adaptive_lambda << ",\n"
            << "  \"express_detour_ratio\": " << o.detour_ratio << ",\n"
            << "  \"express_escape_timeout\": " << o.escape_timeout << ",\n"
            << "  \"injection_attempts\": " << stats.attempts << ",\n"
            << "  \"offered_requests\": " << stats.offers << ",\n"
            << "  \"generated_requests\": " << stats.generated << ",\n"
            << "  \"source_blocked_offers\": " << stats.blocked << ",\n"
            << "  \"packets_injected\": " << stats.injected << ",\n"
            << "  \"packets_received\": " << stats.received << ",\n"
            << "  \"flits_injected\": " << stats.injected*o.packet_flits << ",\n"
            << "  \"flits_received\": " << stats.received*o.packet_flits << ",\n"
            << "  \"initial_send_retries\": " << stats.initial_retries << ",\n"
            << "  \"attempted_throughput\": " << stats.attempts/denom << ",\n"
            << "  \"actual_offered_throughput\": " << stats.offers/denom << ",\n"
            << "  \"generated_request_throughput\": " << stats.generated/denom << ",\n"
            << "  \"injected_throughput\": " << stats.injected/denom << ",\n"
            << "  \"accepted_throughput\": " << stats.received/denom << ",\n"
            << "  \"delivery_fraction\": " << (stats.injected?double(stats.received)/stats.injected:0.0) << ",\n"
            << "  \"average_packet_latency_cycles\": " << (stats.received?double(stats.latency_sum/stats.received):0.0) << ",\n"
            << "  \"per_source_received\": " << vector_json(stats.per_source_received) << ",\n"
            << "  \"per_source_throughput\": " << vector_json(source_throughput) << ",\n"
            << "  \"per_source_average_latency\": " << vector_json(source_latency) << ",\n"
            << "  \"per_source_throughput_jain\": " << source_jain << ",\n"
            << "  \"latency_histogram_upper_bounds\": " << vector_json(latency_bucket_upper) << ",\n"
            << "  \"latency_histogram\": " << vector_json(stats.latency_histogram) << ",\n"
            << "  \"escape_latency_histogram\": " << vector_json(stats.escape_latency_histogram) << ",\n"
            << "  \"adaptive_latency_histogram\": " << vector_json(stats.adaptive_latency_histogram) << ",\n"
            << "  \"average_hops\": " << (stats.received?double(stats.hops_sum/stats.received):0.0) << ",\n"
            << "  \"express_traversals\": " << stats.express_traversals << ",\n"
            << "  \"escape_transitions\": " << stats.escape_transitions << ",\n"
            << "  \"escape_traversals\": " << stats.escape_traversals << ",\n"
            << "  \"escape_transition_delay_cycles\": " << stats.escape_delay << ",\n"
            << "  \"delivered_escape_packets\": " << stats.delivered_escape << ",\n"
            << "  \"delivered_escape_fraction\": " << (stats.received?double(stats.delivered_escape)/stats.received:0.0) << ",\n"
            << "  \"average_cycles_before_escape\": " << (stats.escape_transitions?double(stats.escape_delay)/stats.escape_transitions:0.0) << ",\n"
            << "  \"nonminimal_decisions\": " << stats.nonminimal << ",\n"
            << "  \"reservation_current_total\": " << res_cur << ",\n"
            << "  \"reservation_current_max\": " << res_max << ",\n"
            << "  \"reservation_increments_total\": " << res_inc << ",\n"
            << "  \"reservation_decrements_total\": " << res_dec << ",\n"
            << "  \"reservation_accounting_delta\": " << int64_t(res_inc)-int64_t(res_dec) << ",\n"
            << "  \"source_route_planned_0_express\": " << stats.planned[0] << ",\n"
            << "  \"source_route_planned_1_express\": " << stats.planned[1] << ",\n"
            << "  \"source_route_planned_2_express\": " << stats.planned[2] << ",\n"
            << "  \"delivered_0_express\": " << stats.delivered[0] << ",\n"
            << "  \"delivered_1_express\": " << stats.delivered[1] << ",\n"
            << "  \"delivered_2_express\": " << stats.delivered[2] << ",\n"
            << "  \"delivered_express_bin_total\": " << sumv(stats.delivered) << ",\n"
            << "  \"express_edge_packets_selected\": " << vector_json(stats.edge_selected) << ",\n"
            << "  \"express_q_sample_sum\": " << vector_json(stats.q_sum) << ",\n"
            << "  \"express_q_sample_max\": " << vector_json(stats.q_max) << ",\n"
            << "  \"express_r_sample_sum\": " << vector_json(stats.r_sum) << ",\n"
            << "  \"express_r_sample_max\": " << vector_json(stats.r_max) << ",\n"
            << "  \"express_state_sample_count\": " << stats.state_samples << ",\n"
            << "  \"express_q_sample_average\": " << vector_json(q_average) << ",\n"
            << "  \"express_r_sample_average\": " << vector_json(r_average) << ",\n"
            << "  \"express_info_queries\": " << stats.info_queries << ",\n"
            << "  \"express_info_unknown_queries\": " << stats.info_unknown_queries << ",\n"
            << "  \"express_info_updates\": " << stats.info_updates << ",\n"
            << "  \"express_info_q_mae\": " << (stats.info_queries?double(stats.info_q_abs_error/stats.info_queries):0.0) << ",\n"
            << "  \"express_info_r_mae\": " << (stats.info_queries?double(stats.info_r_abs_error/stats.info_queries):0.0) << ",\n"
            << "  \"express_info_true_q_mean\": " << (stats.info_queries?double(stats.info_true_q_sum/stats.info_queries):0.0) << ",\n"
            << "  \"express_info_observed_q_mean\": " << (stats.info_queries?double(stats.info_observed_q_sum/stats.info_queries):0.0) << ",\n"
            << "  \"express_info_true_r_mean\": " << (stats.info_queries?double(stats.info_true_r_sum/stats.info_queries):0.0) << ",\n"
            << "  \"express_info_observed_r_mean\": " << (stats.info_queries?double(stats.info_observed_r_sum/stats.info_queries):0.0) << ",\n"
            << "  \"express_info_local_pending_mean\": " << (stats.info_queries?double(stats.info_local_pending_sum/stats.info_queries):0.0) << ",\n"
            << "  \"reservation_registration_messages\": " << stats.registration_messages << ",\n"
            << "  \"reservation_registration_acks\": " << stats.registration_acks << ",\n"
            << "  \"reservation_registration_cancels\": " << stats.registration_cancels << ",\n"
            << "  \"reservation_late_registrations\": " << stats.late_registrations << ",\n"
            << "  \"directed_link_sources\": " << vector_json(link_sources) << ",\n"
            << "  \"directed_link_destinations\": " << vector_json(link_destinations) << ",\n"
            << "  \"directed_link_express_ids\": " << vector_json(link_express_ids) << ",\n"
            << "  \"directed_link_utilization\": " << vector_json(util) << ",\n"
            << "  \"max_link_utilization\": " << (ordered.empty()?0.0:ordered.back()) << ",\n"
            << "  \"p95_link_utilization\": " << p95 << ",\n"
            << "  \"link_utilization_cv\": " << (mean?std::sqrt(variance)/mean:0.0) << "\n"
            << "}\n";
    }

  private:
    Options o;
    std::vector<ExpressEdge> edges;
    std::mt19937_64 rng;
    int node_count = 0;
    std::vector<Router> routers;
    std::vector<Ni> nis;
    std::vector<DirectedLink> links;
    std::vector<std::vector<int>> link_index;
    std::vector<std::vector<uint32_t>> edge_latency;
    std::vector<std::vector<uint32_t>> distance;
    std::vector<std::vector<int>> next_hop;
    std::vector<std::vector<int>> neighbors;
    std::vector<std::vector<std::vector<Candidate>>> candidates;
    std::unordered_map<uint64_t,Packet> packets;
    std::vector<std::vector<Arrival>> arrivals;
    std::vector<std::vector<Credit>> credits;
    std::vector<std::vector<ReservationControlEvent>> reservation_control;
    std::vector<int64_t> reservations;
    // Used only by ideal all-link Dijkstra policy 6. Kept separate so the
    // original policy-4 express reservation semantics remain unchanged.
    std::vector<int64_t> link_reservations;
    std::vector<uint32_t> originated_q, originated_r;
    std::vector<uint8_t> originated_valid;
    std::deque<ExpressInfoSnapshot> express_info_history;
    std::vector<std::vector<uint32_t>> local_unacknowledged;
    std::unordered_map<uint64_t,ReservationToken> reservation_tokens;
    Stats stats;
    uint64_t next_packet = 0, cycle = 0, end_cycle = 0;
    uint64_t lifetime_moves = 0, lifetime_deliveries = 0;
    uint64_t last_move_cycle = 0, last_delivery_cycle = 0;
    bool ever_delivered = false;
    bool ni_watchdog_triggered = false, stopped_at_ni_watchdog = false;
    uint64_t ni_watchdog_cycle = 0;
    int ni_watchdog_source = -1;
    uint64_t watchdog_move_age = 0, watchdog_delivery_age = 0;
    uint64_t watchdog_lifetime_moves = 0, watchdog_lifetime_deliveries = 0;
    bool had_delivery_at_watchdog = false;
    uint64_t max_ni_busy_streak = 0, max_ni_busy_streak_cycle = 0;
    uint64_t max_ni_busy_streak_before_drain = 0;
    int max_ni_busy_streak_source = -1;
    bool global_no_progress = false;
    uint64_t global_no_progress_cycle = 0;
    bool drain_completed = false;
    uint64_t drain_completion_cycle = 0;
    std::vector<uint64_t> debug_consecutive_ni_busy;

    bool measuring(uint64_t when) const {
        return when >= o.warmup && when < o.warmup + o.measurement;
    }

    uint64_t pending_source_messages() const {
        uint64_t total = 0;
        for (const auto &ni : nis) total += ni.messages.size();
        return total;
    }

    bool data_empty() const {
        if (!packets.empty() || pending_source_messages()) return false;
        for (const auto &ni : nis)
            for (const auto packet : ni.slots)
                if (packet != kEmpty) return false;
        return true;
    }

    std::string termination_reason() const {
        if (global_no_progress) return "global_no_progress";
        if (stopped_at_ni_watchdog) return "ni_watchdog";
        if (drain_completed)
            return ni_watchdog_triggered ?
                "drain_completed_after_ni_watchdog" : "drain_completed";
        if (ni_watchdog_triggered) return "simulate_limit_after_ni_watchdog";
        return "simulate_limit";
    }

    void record_move() {
        ++lifetime_moves;
        last_move_cycle = cycle;
    }

    void record_delivery() {
        ++lifetime_deliveries;
        last_delivery_cycle = cycle;
        ever_delivered = true;
    }

    void detect_global_no_progress() {
        if (!packets.empty() && cycle > last_move_cycle &&
            cycle - last_move_cycle > o.deadlock_threshold) {
            global_no_progress = true;
            global_no_progress_cycle = cycle;
            if (!o.deadlock_trace.empty()) dump_deadlock_trace();
        }
    }

    void dump_deadlock_trace() const {
        std::ofstream output(o.deadlock_trace);
        if (!output) fail("cannot write deadlock trace: " + o.deadlock_trace);
        output << "{\n"
               << "  \"cycle\": " << cycle << ",\n"
               << "  \"last_move_cycle\": " << last_move_cycle << ",\n"
               << "  \"live_packets\": " << packets.size() << ",\n"
               << "  \"waits\": [\n";
        bool first_wait = true;
        for (int router = 0; router < node_count; ++router) {
            for (size_t inport = 0; inport < routers[router].inputs.size();
                 ++inport) {
                const int held_link = routers[router].input_upstream[inport];
                const auto &input = routers[router].inputs[inport];
                for (int input_vc = 0; input_vc < int(o.vcs_per_vnet);
                     ++input_vc) {
                    const auto &slot = input.slots[input_vc];
                    if (slot.packet == kEmpty || slot.enqueue > cycle)
                        continue;
                    const auto found = packets.find(slot.packet);
                    if (found == packets.end()) continue;
                    const Packet &packet = found->second;
                    if (free_vc_for_packet(router, slot.outport, packet) >= 0)
                        continue;
                    if (!first_wait) output << ",\n";
                    first_wait = false;
                    output << "    {\"packet\": " << packet.id
                           << ", \"source\": " << packet.src
                           << ", \"destination\": " << packet.dest
                           << ", \"router\": " << router
                           << ", \"input_port\": " << inport
                           << ", \"input_vc\": " << input_vc
                           << ", \"held_link\": " << held_link
                           << ", \"requested_outport\": " << slot.outport
                           << ", \"requested_link\": ";
                    int requested_link = -1;
                    if (slot.outport > 0 &&
                        size_t(slot.outport) <= routers[router].outgoing.size())
                        requested_link = routers[router].outgoing[
                            slot.outport - 1];
                    output << requested_link << ", \"blockers\": [";
                    const auto &busy = slot.outport == 0 ?
                        routers[router].local_busy : links[requested_link].busy;
                    bool first_blocker = true;
                    const int begin_vc = packet.escape && !o.no_escape ?
                        int(o.vcs_per_vnet) - 1 : 0;
                    const int end_vc = packet.escape && !o.no_escape ?
                        int(o.vcs_per_vnet) : adaptive_vc_count();
                    for (int output_vc = begin_vc; output_vc < end_vc;
                         ++output_vc) {
                        if (!busy[output_vc]) continue;
                        uint64_t blocker = kEmpty;
                        if (requested_link >= 0) {
                            const auto &link = links[requested_link];
                            blocker = routers[link.dst].inputs[link.inport]
                                .slots[output_vc].packet;
                        }
                        if (!first_blocker) output << ", ";
                        first_blocker = false;
                        output << "{\"vc\": " << output_vc
                               << ", \"packet\": ";
                        if (blocker == kEmpty) output << "null";
                        else output << blocker;
                        output << "}";
                    }
                    output << "]}";
                }
            }
        }
        output << "\n  ],\n  \"links\": [\n";
        for (size_t index = 0; index < links.size(); ++index) {
            const auto &link = links[index];
            output << "    {\"id\": " << index
                   << ", \"source\": " << link.src
                   << ", \"destination\": " << link.dst
                   << ", \"express_id\": " << link.express_id << "}";
            output << (index + 1 == links.size() ? "\n" : ",\n");
        }
        output << "  ]\n}\n";
    }

    void record_ni_watchdog(int source) {
        if (ni_watchdog_triggered) return;
        ni_watchdog_triggered = true;
        ni_watchdog_cycle = cycle;
        ni_watchdog_source = source;
        watchdog_move_age = cycle - last_move_cycle;
        had_delivery_at_watchdog = ever_delivered;
        watchdog_delivery_age = ever_delivered ? cycle - last_delivery_cycle : 0;
        watchdog_lifetime_moves = lifetime_moves;
        watchdog_lifetime_deliveries = lifetime_deliveries;
    }

    template<class T> T random_int(T lo, T hi) {
        return std::uniform_int_distribution<T>(lo,hi)(rng);
    }

    void reset_stats() {
        stats = Stats{};
        stats.per_source_received.assign(node_count,0);
        stats.per_source_latency_sum.assign(node_count,0);
        const size_t n = 2*edges.size();
        const size_t stat_n=std::max<size_t>(1,n);
        stats.reservation_inc.assign(stat_n,0); stats.reservation_dec.assign(stat_n,0);
        stats.edge_selected.assign(stat_n,0); stats.q_sum.assign(stat_n,0);
        stats.q_max.assign(stat_n,0); stats.r_sum.assign(stat_n,0); stats.r_max.assign(stat_n,0);
        for (auto &link : links) link.activity = 0;
    }

    static size_t latency_bucket(uint64_t latency) {
        size_t bucket=0;
        while(latency>1 && bucket+1<32) {
            latency=(latency+1)/2;
            ++bucket;
        }
        return bucket;
    }

    void add_link(int src, int dst, int latency, int express_id) {
        DirectedLink link;
        link.src=src; link.dst=dst; link.latency=latency; link.express_id=express_id;
        link.busy.assign(o.vcs_per_vnet,false);
        link.outport = int(routers[src].outgoing.size()) + 1;
        link.inport = int(routers[dst].inputs.size());
        const int index = links.size();
        links.push_back(link);
        routers[src].outgoing.push_back(index);
        routers[dst].inputs.emplace_back(o.vcs_per_vnet);
        routers[dst].input_upstream.push_back(index);
        link_index[src][dst]=index;
        edge_latency[src][dst]=latency;
        neighbors[src].push_back(dst);
    }

    void build_graph() {
        const uint32_t inf=std::numeric_limits<uint32_t>::max()/4;
        for (int u=0;u<node_count;++u) {
            routers[u].inputs.emplace_back(o.vcs_per_vnet); // Local input first.
            routers[u].input_upstream.push_back(-1);
            std::fill(edge_latency[u].begin(),edge_latency[u].end(),inf);
        }
        // Match ExpressMesh.py link creation order.
        for (int row=0;row<o.dimension;++row)
            for (int col=0;col<o.dimension-1;++col) {
            const int west=row*o.dimension+col, east=west+1;
            add_link(west,east,o.mesh_link_latency,-1);
            add_link(east,west,o.mesh_link_latency,-1);
        }
        for (int col=0;col<o.dimension;++col)
            for (int row=0;row<o.dimension-1;++row) {
            const int south=row*o.dimension+col, north=south+o.dimension;
            add_link(south,north,o.mesh_link_latency,-1);
            add_link(north,south,o.mesh_link_latency,-1);
        }
        for (size_t i=0;i<edges.size();++i) {
            add_link(edges[i].u,edges[i].v,edges[i].latency,int(2*i));
            add_link(edges[i].v,edges[i].u,edges[i].latency,int(2*i+1));
        }
        for (int u=0;u<node_count;++u) {
            std::sort(neighbors[u].begin(),neighbors[u].end());
            routers[u].rr_link_in.assign(routers[u].outgoing.size(),0);
        }
        for (int d=0;d<node_count;++d) {
            std::vector<uint32_t> dist(node_count,inf); dist[d]=0;
            using Q=std::pair<uint32_t,int>;
            std::priority_queue<Q,std::vector<Q>,std::greater<Q>> q;
            q.push({0,d});
            while(!q.empty()) {
                auto [du,u]=q.top();q.pop(); if(du!=dist[u])continue;
                for(int v:neighbors[u]) {
                    const uint32_t nd=du+edge_latency[u][v];
                    if(nd<dist[v]){dist[v]=nd;q.push({nd,v});}
                }
            }
            for(int s=0;s<node_count;++s){
                distance[s][d]=dist[s]; next_hop[s][d]=-1;
                if(s==d)continue;
                for(int v:neighbors[s]) if(edge_latency[s][v]+dist[v]==dist[s] &&
                    (next_hop[s][d]<0 || v<next_hop[s][d])) next_hop[s][d]=v;
                if(next_hop[s][d]<0) fail("disconnected topology");
            }
        }
    }

    std::vector<int> xy_segment(int source,int dest) const {
        int x=source%o.dimension,y=source/o.dimension;
        const int dx=dest%o.dimension,dy=dest/o.dimension;
        std::vector<int> p{source};
        while(x!=dx){x += dx>x?1:-1;p.push_back(y*o.dimension+x);}
        while(y!=dy){y += dy>y?1:-1;p.push_back(y*o.dimension+x);}
        return p;
    }

    void verify_escape_cdg() const {
        std::vector<std::vector<int>> dependencies(links.size());
        std::vector<int> indegree(links.size(), 0);
        std::vector<bool> escape_channel(links.size(), false);
        for (int source = 0; source < node_count; ++source) {
            for (int dest = 0; dest < node_count; ++dest) {
                if (source == dest) continue;
                const auto path = xy_segment(source, dest);
                std::vector<int> channels;
                for (size_t i = 1; i < path.size(); ++i) {
                    const int channel = link_index[path[i-1]][path[i]];
                    if (channel < 0 || links[channel].express_id >= 0)
                        fail("escape XY path is not confined to the base mesh");
                    escape_channel[channel] = true;
                    channels.push_back(channel);
                }
                for (size_t i = 1; i < channels.size(); ++i) {
                    auto &next = dependencies[channels[i-1]];
                    if (std::find(next.begin(), next.end(), channels[i]) == next.end()) {
                        next.push_back(channels[i]);
                        ++indegree[channels[i]];
                    }
                }
            }
        }
        std::queue<int> ready;
        int channel_count = 0, visited = 0;
        for (size_t i = 0; i < links.size(); ++i) {
            if (!escape_channel[i]) continue;
            ++channel_count;
            if (indegree[i] == 0) ready.push(i);
        }
        while (!ready.empty()) {
            const int channel = ready.front();
            ready.pop();
            ++visited;
            for (const int next : dependencies[channel])
                if (--indegree[next] == 0) ready.push(next);
        }
        if (visited != channel_count)
            fail("escape VC channel-dependency graph contains a cycle");
    }

    void add_candidate(int s,int d,const std::vector<int>& ids) {
        std::vector<int> path{ s };
        int current=s, latency=0;
        for(size_t i=0;i<ids.size();++i) {
            const int id=ids[i], edge=id/2;
            const int entry=(id%2==0)?edges[edge].u:edges[edge].v;
            const int exit=(id%2==0)?edges[edge].v:edges[edge].u;
            auto seg=xy_segment(current,entry);
            path.insert(path.end(),seg.begin()+1,seg.end()); latency+=int(seg.size())-1;
            path.push_back(exit); latency+=edges[edge].latency; current=exit;
        }
        auto seg=xy_segment(current,d);
        path.insert(path.end(),seg.begin()+1,seg.end()); latency+=int(seg.size())-1;
        auto sorted=path;std::sort(sorted.begin(),sorted.end());
        if(std::adjacent_find(sorted.begin(),sorted.end())!=sorted.end())return;
        candidates[s][d].push_back(Candidate{latency,ids});
    }

    void build_candidates() {
        const int directed=2*edges.size();
        const auto less=[](const Candidate&a,const Candidate&b){
                // Match configs/topologies/ExpressMesh.py exactly: when two
                // routes have the same static latency, fewer express hops win
                // before the directed express IDs break the final tie.
                if (a.latency != b.latency) return a.latency < b.latency;
                if (a.express_ids.size() != b.express_ids.size())
                    return a.express_ids.size() < b.express_ids.size();
                return a.express_ids < b.express_ids;
        };
        const auto entry=[&](int id) {
            return id%2==0?edges[id/2].u:edges[id/2].v;
        };
        const auto exit=[&](int id) {
            return id%2==0?edges[id/2].v:edges[id/2].u;
        };
        for(int d=0;d<node_count;++d) {
            // For fixed first express edge and destination, second-edge costs
            // do not depend on the source.  Sorting these lists once lets a
            // k-way merge find the exact useful pair prefix instead of
            // materializing O(E^2) routes for every (source,destination).
            std::vector<std::vector<int>> second_order(directed);
            for(int a=0;a<directed;++a) {
                auto &order=second_order[a];
                for(int b=0;b<directed;++b)if(a!=b)order.push_back(b);
                std::sort(order.begin(),order.end(),[&](int b,int c) {
                    const int bc=mesh_distance(exit(a),entry(b))+
                        edges[b/2].latency+mesh_distance(exit(b),d);
                    const int cc=mesh_distance(exit(a),entry(c))+
                        edges[c/2].latency+mesh_distance(exit(c),d);
                    return bc!=cc?bc<cc:b<c;
                });
            }
            for(int s=0;s<node_count;++s)if(s!=d) {
                add_candidate(s,d,{});
                for(int a=0;a<directed;++a)add_candidate(s,d,{a});
                auto &v=candidates[s][d];
                std::sort(v.begin(),v.end(),less);
                if(v.size()>o.source_route_candidates)
                    v.resize(o.source_route_candidates);
                using PairItem=std::tuple<int,int,int,int>;
                std::priority_queue<PairItem,std::vector<PairItem>,
                    std::greater<PairItem>> ready;
                for(int a=0;a<directed;++a)if(!second_order[a].empty()) {
                    const int b=second_order[a][0];
                    const int cost=mesh_distance(s,entry(a))+
                        edges[a/2].latency+mesh_distance(exit(a),entry(b))+
                        edges[b/2].latency+mesh_distance(exit(b),d);
                    ready.push({cost,a,b,0});
                }
                while(!ready.empty()) {
                    const auto [cost,a,b,position]=ready.top();ready.pop();
                    if(v.size()>=o.source_route_candidates &&
                       cost>v.back().latency)break;
                    const size_t before=v.size();
                    add_candidate(s,d,{a,b});
                    if(v.size()!=before) {
                        std::sort(v.begin(),v.end(),less);
                        if(v.size()>o.source_route_candidates)
                            v.resize(o.source_route_candidates);
                    }
                    const int next_position=position+1;
                    if(next_position<int(second_order[a].size())) {
                        const int next_b=second_order[a][next_position];
                        const int next_cost=mesh_distance(s,entry(a))+
                            edges[a/2].latency+
                            mesh_distance(exit(a),entry(next_b))+
                            edges[next_b/2].latency+
                            mesh_distance(exit(next_b),d);
                        ready.push({next_cost,a,next_b,next_position});
                    }
                }
                // The original top-K implementation can evict the pure XY
                // route when K express-assisted routes have lower static
                // latency.  Keep this optional so experiments can compare
                // top-K against top-(K-1)+mesh without silently changing the
                // historical default.
                if(o.retain_mesh_candidate &&
                   std::none_of(v.begin(),v.end(),[](const Candidate &route) {
                       return route.express_ids.empty();
                   })) {
                    Candidate mesh{mesh_distance(s,d),{}};
                    if(v.size()>=o.source_route_candidates)v.back()=mesh;
                    else v.push_back(mesh);
                    std::sort(v.begin(),v.end(),less);
                }
            }
        }
    }

    void generate_traffic() {
        // The default period of two matches the 1 GHz tester / 2 GHz network.
        if (cycle % o.injection_period) return;
        for(int src=0;src<node_count;++src) {
            const unsigned draw=random_int<unsigned>(0,1000);
            if(double(draw)>=o.rate*1000.0)continue;
            if(measuring(cycle))stats.attempts++;
            if(o.traffic=="cutstress" &&
               src%o.dimension>=o.dimension/2)continue;
            if(measuring(cycle)){stats.offers++;stats.generated++;}
            int dest=src;
            if(o.traffic=="uniform_random")
                dest=random_int<int>(0,node_count-1);
            else if(o.traffic=="cutstress" ||
                    o.traffic=="cutstress_bidirectional")
                dest=(src/o.dimension)*o.dimension+
                    (o.dimension-1-src%o.dimension);
            else if(o.traffic=="bit_complement") dest=node_count-1-src;
            else if(o.traffic=="tornado")
                dest=(src/o.dimension)*o.dimension+
                    (src%o.dimension+o.dimension/2-1)%o.dimension;
            else if(o.traffic=="soc_heterogeneous")
                dest=sample_soc_destination(src);
            else {
                const int h0=(o.dimension/2-1)*o.dimension+o.dimension/2-1;
                const int h1=(o.dimension/2)*o.dimension+o.dimension/2;
                if(random_int<int>(0,1)==0) {
                    dest=random_int<int>(0,1)==0?h0:h1;
                    if(dest==src)dest=(dest==h0?h1:h0);
                } else do {dest=random_int<int>(0,node_count-1);} while(dest==src);
            }
            // One Ruby protocol cycle from tester request to network message.
            nis[src].messages.push_back(Pending{dest,cycle+1});
        }
    }

    int sample_soc_destination(int src) {
        // A compact heterogeneous-SoC model.  Sources mostly communicate
        // inside a 4x4 compute tile, but also access one bank per tile, eight
        // edge memory controllers, and eight shared accelerator endpoints.
        // Shared memory and accelerators each receive 5%; the final 45% is
        // diffuse background coherence/global communication.  The resulting
        // nominally non-local classes yield about 51.7% actual inter-tile
        // traffic at 16x16, while no shared endpoint is an unavoidable
        // bottleneck.
        const int tile=std::max(2,o.dimension/4);
        const int x=src%o.dimension,y=src/o.dimension;
        const int tile_x=std::min(o.dimension-1,(x/tile)*tile+tile/2);
        const int tile_y=std::min(o.dimension-1,(y/tile)*tile+tile/2);
        const unsigned category=random_int<unsigned>(0,9999);
        int dest=src;
        if(category<3000) {
            const int x0=(x/tile)*tile,y0=(y/tile)*tile;
            do {
                const int dx=random_int<int>(x0,
                    std::min(o.dimension-1,x0+tile-1));
                const int dy=random_int<int>(y0,
                    std::min(o.dimension-1,y0+tile-1));
                dest=dy*o.dimension+dx;
            } while(dest==src);
        } else if(category<4500) {
            dest=tile_y*o.dimension+tile_x;
        } else if(category<5000) {
            const std::array<std::pair<int,int>,8> memory{{
                {0,o.dimension/8},{0,3*o.dimension/8},
                {0,5*o.dimension/8},{0,7*o.dimension/8},
                {o.dimension-1,o.dimension/8},
                {o.dimension-1,3*o.dimension/8},
                {o.dimension-1,5*o.dimension/8},
                {o.dimension-1,7*o.dimension/8}}};
            const auto [dx,dy]=memory[random_int<size_t>(0,memory.size()-1)];
            dest=std::min(o.dimension-1,dy)*o.dimension+dx;
        } else if(category<5500) {
            const std::array<std::pair<int,int>,8> accelerators{{
                {o.dimension/4,o.dimension/4},
                {o.dimension/2,o.dimension/4},
                {3*o.dimension/4,o.dimension/4},
                {o.dimension/4,o.dimension/2},
                {3*o.dimension/4,o.dimension/2},
                {o.dimension/4,3*o.dimension/4},
                {o.dimension/2,3*o.dimension/4},
                {3*o.dimension/4,3*o.dimension/4}}};
            const auto [dx,dy]=accelerators[
                random_int<size_t>(0,accelerators.size()-1)];
            dest=std::min(o.dimension-1,dy)*o.dimension+
                std::min(o.dimension-1,dx);
        } else do {
            dest=random_int<int>(0,node_count-1);
        } while(dest==src);
        if(dest==src)dest=(src+1)%node_count;
        return dest;
    }

    int adaptive_vc_count() const {
        if (!o.no_escape) return int(o.vcs_per_vnet)-1;
        // Reproduce OutputUnit::{has_free_vc,select_free_vc}: its non-escape
        // end iterator excludes VC3 even when express_escape_enabled is false.
        return o.correct_no_escape_vcs ? int(o.vcs_per_vnet) :
                                         int(o.vcs_per_vnet)-1;
    }

    int free_vc_for_output(int router,int outport,bool escape) const {
        const auto &busy = outport==0 ? routers[router].local_busy :
            links[routers[router].outgoing[outport-1]].busy;
        const int escape_vc=int(o.vcs_per_vnet)-1;
        if(escape && !o.no_escape)return busy[escape_vc]?-1:escape_vc;
        for(int vc=0;vc<adaptive_vc_count();++vc)if(!busy[vc])return vc;
        return -1;
    }

    int free_vc_for_packet(int router,int outport,const Packet &p) const {
        if(p.escape || (o.source_mesh_routing!="phase_xy" &&
                        o.source_mesh_routing!="monotonic_xy") ||
           !p.source_routed)
            return free_vc_for_output(router,outport,p.escape);
        const auto &busy=outport==0?routers[router].local_busy:
            links[routers[router].outgoing[outport-1]].busy;
        const size_t stage=std::min<size_t>(2,p.express_stage);
        if(o.source_mesh_routing=="phase_xy") {
            const int vc=p.phase_vc[stage];
            return busy[vc]?-1:vc;
        }
        for(int vc=p.phase_vc_low[stage];vc<=p.phase_vc_high[stage];++vc)
            if(!busy[vc])return vc;
        return -1;
    }

    double congestion(int router,int outport) const {
        const auto &busy = outport==0 ? routers[router].local_busy :
            links[routers[router].outgoing[outport-1]].busy;
        int count=0,n=adaptive_vc_count();for(int i=0;i<n;++i)count+=busy[i];
        return n?double(count)/n:1.0;
    }

    int output_to_neighbor(int router,int neighbor) const {
        const int li=link_index[router][neighbor];
        if(li<0)fail("missing directed link");
        return links[li].outport;
    }

    int local_adaptive(int current,int target) {
        const int cx=current%o.dimension,cy=current/o.dimension;
        const int tx=target%o.dimension,ty=target/o.dimension;
        std::vector<int> productive;
        if(tx>cx)productive.push_back(current+1);
        if(tx<cx)productive.push_back(current-1);
        if(ty>cy)productive.push_back(current+o.dimension);
        if(ty<cy)productive.push_back(current-o.dimension);
        std::vector<int> usable;
        for(int n:productive)if(free_vc_for_output(current,output_to_neighbor(current,n),false)>=0)
            usable.push_back(n);
        if(usable.empty())usable=productive;
        if(usable.empty())return 0;
        double best=std::numeric_limits<double>::infinity();std::vector<int> ties;
        for(int n:usable){int out=output_to_neighbor(current,n);double c=congestion(current,out);
            if(c<best){best=c;ties={n};}else if(c==best)ties.push_back(n);}
        return output_to_neighbor(current,ties[random_int<size_t>(0,ties.size()-1)]);
    }

    int xy_output(int current, int target) const {
        if (current == target) return 0;
        const int x=current%o.dimension,y=current/o.dimension;
        const int tx=target%o.dimension,ty=target/o.dimension;
        if(x!=tx)return output_to_neighbor(current,current+(tx>x?1:-1));
        return output_to_neighbor(
            current,current+(ty>y?o.dimension:-o.dimension));
    }

    int yx_output(int current,int target) const {
        if(current==target)return 0;
        const int x=current%o.dimension,y=current/o.dimension;
        const int tx=target%o.dimension,ty=target/o.dimension;
        if(y!=ty)return output_to_neighbor(
            current,current+(ty>y?o.dimension:-o.dimension));
        return output_to_neighbor(current,current+(tx>x?1:-1));
    }

    int dor_adaptive_output(Packet &p,int current,int target) {
        const size_t stage=std::min<size_t>(2,p.express_stage);
        auto &yx=p.segment_yx[stage];
        if(yx<0) {
            const int x=current%o.dimension,y=current/o.dimension;
            const int tx=target%o.dimension,ty=target/o.dimension;
            if(x==tx)yx=1;
            else if(y==ty)yx=0;
            else {
                const int xout=xy_output(current,target);
                const int yout=yx_output(current,target);
                const double xc=congestion(current,xout),yc=congestion(current,yout);
                if(yc<xc)yx=1;
                else if(xc<yc)yx=0;
                else yx=random_int<int>(0,1);
            }
        }
        return yx?yx_output(current,target):xy_output(current,target);
    }

    int west_first_output(int current,int target) {
        if(current==target)return 0;
        const int x=current%o.dimension,y=current/o.dimension;
        const int tx=target%o.dimension,ty=target/o.dimension;
        std::vector<int> productive;
        // West-first forbids every turn into West: when West is needed it is
        // completed before the packet may take a vertical channel.  The
        // remaining East/North/South choices are selected adaptively.
        if(tx<x)productive.push_back(current-1);
        else {
            if(tx>x)productive.push_back(current+1);
            if(ty>y)productive.push_back(current+o.dimension);
            if(ty<y)productive.push_back(current-o.dimension);
        }
        std::vector<int> usable;
        for(int n:productive)
            if(free_vc_for_output(current,output_to_neighbor(current,n),false)>=0)
                usable.push_back(n);
        if(usable.empty())usable=productive;
        double best=std::numeric_limits<double>::infinity();
        std::vector<int> ties;
        for(int n:usable) {
            const double c=congestion(current,output_to_neighbor(current,n));
            if(c<best){best=c;ties={n};}else if(c==best)ties.push_back(n);
        }
        return output_to_neighbor(current,
            ties[random_int<size_t>(0,ties.size()-1)]);
    }

    int odd_even_output(int segment_source,int current,int target) {
        if(current==target)return 0;
        const int sx=segment_source%o.dimension;
        const int x=current%o.dimension,y=current/o.dimension;
        const int tx=target%o.dimension,ty=target/o.dimension;
        const int dx=tx-x;
        std::vector<int> productive;
        auto add_vertical=[&] {
            if(ty>y)productive.push_back(current+o.dimension);
            if(ty<y)productive.push_back(current-o.dimension);
        };
        if(dx==0)add_vertical();
        else if(dx>0) {
            // Chiu's odd-even turn model: no E->N/S turns in even columns
            // and no N/S->E turns in odd columns (with endpoint exceptions).
            if(ty==y)productive.push_back(current+1);
            else {
                if((x&1) || x==sx)add_vertical();
                if((tx&1) || dx!=1)productive.push_back(current+1);
            }
        } else {
            productive.push_back(current-1);
            if(!(x&1))add_vertical();
        }
        if(productive.empty())fail("odd-even routing produced no productive output");
        std::vector<int> usable;
        for(int n:productive)
            if(free_vc_for_output(current,output_to_neighbor(current,n),false)>=0)
                usable.push_back(n);
        if(usable.empty())usable=productive;
        double best=std::numeric_limits<double>::infinity();
        std::vector<int> ties;
        for(int n:usable) {
            const double c=congestion(current,output_to_neighbor(current,n));
            if(c<best){best=c;ties={n};}else if(c==best)ties.push_back(n);
        }
        return output_to_neighbor(current,
            ties[random_int<size_t>(0,ties.size()-1)]);
    }

    int source_mesh_output(Packet &p,int current,int target) {
        if(o.source_mesh_routing=="xy" || o.source_mesh_routing=="phase_xy" ||
           o.source_mesh_routing=="monotonic_xy")
            return xy_output(current,target);
        if(o.source_mesh_routing=="dor_adaptive")
            return dor_adaptive_output(p,current,target);
        if(o.source_mesh_routing=="odd_even") {
            int segment_source=p.src;
            if(p.express_stage>0) {
                const int previous=p.express_ids[p.express_stage-1];
                segment_source=previous%2==0?edges[previous/2].v:edges[previous/2].u;
            }
            return odd_even_output(segment_source,current,target);
        }
        if(o.source_mesh_routing=="west_first")return west_first_output(current,target);
        return local_adaptive(current,target);
    }

    int compute_route(Packet &p,int current) {
        if(current==p.dest)return 0;
        if(p.escape) return xy_output(current, p.dest);
        if(!p.planned_links.empty()) {
            if(p.planned_link_stage>=p.planned_links.size())
                fail("dynamic source route ended before destination");
            const int link_id=p.planned_links[p.planned_link_stage];
            if(links[link_id].src!=current)
                fail("dynamic source route is not contiguous");
            return links[link_id].outport;
        }
        if(p.source_routed) {
            if(p.express_stage<p.express_ids.size()) {
                const int id=p.express_ids[p.express_stage],e=id/2;
                const int entry=id%2==0?edges[e].u:edges[e].v;
                const int exit=id%2==0?edges[e].v:edges[e].u;
                if(current!=entry)return source_mesh_output(p,current,entry);
                return output_to_neighbor(current,exit);
            }
            return source_mesh_output(p,current,p.dest);
        }
        int next=next_hop[current][p.dest];
        if(o.routing=="adaptive") {
            const uint32_t min_dist=distance[current][p.dest];
            const int min_out=output_to_neighbor(current,next);
            if(congestion(current,min_out)>=o.adaptive_threshold) {
                double best=std::numeric_limits<double>::infinity();
                std::vector<int> ties;
                for(int n:neighbors[current]) {
                    if(distance[n][p.dest]>=min_dist)continue;
                    const uint32_t length=edge_latency[current][n]+distance[n][p.dest];
                    if(double(length)>o.detour_ratio*min_dist)continue;
                    const double score=length*(1.0+o.adaptive_lambda*
                        congestion(current,output_to_neighbor(current,n)));
                    if(score<best){best=score;ties={n};}else if(score==best)ties.push_back(n);
                }
                if(!ties.empty())next=ties[random_int<size_t>(0,ties.size()-1)];
            }
            if(edge_latency[current][next]+distance[next][p.dest]>min_dist && measuring(cycle))
                stats.nonminimal++;
        }
        return output_to_neighbor(current,next);
    }

    void process_credits() {
        auto &events=credits[cycle%credits.size()];
        for(const auto &c:events) {
            if(c.kind==Credit::NiVc)nis[c.owner].busy[c.vc]=false;
            else if(c.kind==Credit::LinkVc)links[c.owner].busy[c.vc]=false;
            else routers[c.owner].local_busy[c.vc]=false;
        }
        events.clear();
    }

    void process_arrivals() {
        auto &events=arrivals[cycle%arrivals.size()];
        for(const auto &a:events) {
            auto it=packets.find(a.packet); if(it==packets.end())fail("arrival for missing packet");
            Packet &p=it->second;
            if(a.kind==Arrival::Ejection) {
                record_delivery();
                if(measuring(cycle)) {
                    const uint64_t latency=cycle-p.created;
                    stats.received++;stats.latency_sum+=latency;
                    stats.hops_sum += p.hops;
                    stats.per_source_received[p.src]++;
                    stats.per_source_latency_sum[p.src]+=latency;
                    const size_t bucket=latency_bucket(latency);
                    stats.latency_histogram[bucket]++;
                    (p.escape?stats.escape_latency_histogram:
                        stats.adaptive_latency_histogram)[bucket]++;
                    if(p.escape)stats.delivered_escape++;
                    if(p.source_routed)stats.delivered[std::min<int>(2,p.express_traversed)]++;
                }
                // The credit traverses Garnet's NI/crossbar event boundary
                // before the router can consume it; the measured effective
                // delay in network cycles is two.
                credits[(cycle+2)%credits.size()].push_back(
                    Credit{Credit::LocalVc,a.router,a.vc});
                packets.erase(it); continue;
            }
            auto &slot=routers[a.router].inputs[a.inport].slots[a.vc];
            if(slot.packet!=kEmpty)fail("downstream input VC overflow");
            slot.packet=a.packet;
            slot.enqueue=cycle+o.router_latency-1;
            slot.outport=compute_route(p,a.router);
        }
        events.clear();
    }

    void transition_escape(Packet &p,InputSlot &slot,int router) {
        if(p.escape)return;
        for(size_t i=p.express_stage;i<p.express_ids.size();++i) {
            cancel_registered_reservation(p,i,router);
        }
        p.express_stage=p.express_ids.size();p.escape=true;
        if(o.source_route_policy==6) {
            for(size_t i=p.planned_link_stage;i<p.planned_links.size();++i) {
                const int link_id=p.planned_links[i];
                if(--link_reservations[link_id]<0)
                    fail("all-link reservation underflow on escape");
            }
        }
        p.planned_link_stage=p.planned_links.size();
        if(measuring(cycle)){
            stats.escape_transitions++;stats.escape_delay+=cycle-p.injected;
        }
        slot.outport=compute_route(p,router);
    }

    void route_and_arbitrate() {
        for(int r=0;r<node_count;++r) {
            Router &router=routers[r];
            struct Request{int input,vc,out;};std::vector<Request> reqs;
            for(size_t ip=0;ip<router.inputs.size();++ip) {
                auto &port=router.inputs[ip];
                for(int n=0;n<int(o.vcs_per_vnet);++n) {
                    const int vc=(port.rr_vc+n)%int(o.vcs_per_vnet);
                    auto &slot=port.slots[vc];
                    if(slot.packet==kEmpty)continue;
                    if(slot.enqueue>cycle)continue;
                    Packet &p=packets.at(slot.packet);
                    if(!o.no_escape && !p.escape && cycle-slot.enqueue>=o.escape_timeout &&
                       free_vc_for_packet(r,slot.outport,p)<0)
                        transition_escape(p,slot,r);
                    if(free_vc_for_packet(r,slot.outport,p)>=0) {
                        reqs.push_back(Request{int(ip),vc,slot.outport});break;
                    }
                }
            }
            // One winner per output, starting from that output's RR input.
            std::vector<int> outputs;outputs.push_back(0);
            for(int li:router.outgoing)outputs.push_back(links[li].outport);
            for(int out:outputs) {
                if(out>0 &&
                   router.outgoing.size()>=size_t(out) &&
                   links[router.outgoing[out-1]].next_send_cycle>cycle)
                    continue;
                int &rr=(out==0?router.rr_local_in:
                    router.rr_link_in[out-1]);
                Request *winner=nullptr;
                for(size_t step=0;step<router.inputs.size();++step){
                    int ip=(rr+step)%router.inputs.size();
                    for(auto &q:reqs)if(q.input==ip && q.out==out){winner=&q;break;}
                    if(winner)break;
                }
                if(!winner)continue;
                auto &port=router.inputs[winner->input];auto &slot=port.slots[winner->vc];
                Packet &p=packets.at(slot.packet);
                const int outvc=free_vc_for_packet(r,out,p);
                if(outvc<0)continue;
                if(out==0)router.local_busy[outvc]=true;
                else links[router.outgoing[out-1]].busy[outvc]=true;
                const uint64_t pid=slot.packet;
                record_move();
                // Free the upstream VC after the one-cycle credit link setup.
                const uint64_t tail_delay=o.packet_flits-1;
                if(winner->input==0)
                    // A credit returning to an NI is consumed after that
                    // cycle's NI injection scheduling, so its first usable
                    // allocation opportunity is effectively two cycles
                    // after downstream SA.
                    credits[(cycle+2+tail_delay)%credits.size()].push_back(
                        Credit{Credit::NiVc,r,winner->vc});
                else {
                    const int upstream=router.input_upstream[winner->input];
                    if(upstream<0)fail("cannot find upstream link");
                    credits[(cycle+2+tail_delay)%credits.size()].push_back(
                        Credit{Credit::LinkVc,upstream,winner->vc});
                }
                if(p.escape && measuring(cycle))stats.escape_traversals++;
                if(out==0)arrivals[(cycle+2+tail_delay)%arrivals.size()].push_back(
                    Arrival{Arrival::Ejection,r,0,outvc,pid});
                else {
                    const int traversed_link=router.outgoing[out-1];
                    DirectedLink &link=links[traversed_link];
                    if(!p.escape && !p.planned_links.empty()) {
                        if(p.planned_link_stage>=p.planned_links.size() ||
                           p.planned_links[p.planned_link_stage]!=traversed_link)
                            fail("packet deviated from dynamic source route");
                        if(o.source_route_policy==6 &&
                           --link_reservations[traversed_link]<0)
                            fail("all-link reservation underflow on traversal");
                        p.planned_link_stage++;
                    }
                    if(link.express_id>=0) {
                        if(measuring(cycle))stats.express_traversals++;
                        if(p.source_routed && p.express_stage<p.express_ids.size()) {
                            const int expected=p.express_ids[p.express_stage];
                            if(expected==link.express_id) {
                                traverse_registered_reservation(
                                    p,p.express_stage);
                                p.express_stage++;p.express_traversed++;
                            }
                        }
                    }
                    p.hops++;
                    arrivals[(cycle+1+link.latency)%arrivals.size()].push_back(
                        Arrival{Arrival::RouterFlit,link.dst,link.inport,outvc,pid});
                    link.next_send_cycle=cycle+o.packet_flits;
                    for(uint32_t flit=0;flit<o.packet_flits;++flit) {
                        link.output_ready_cycles.push_back(cycle+flit+1);
                        if(measuring(cycle+flit+1))link.activity++;
                    }
                }
                slot.packet=kEmpty;slot.outport=-1;
                port.rr_vc=(winner->vc+1)%int(o.vcs_per_vnet);
                rr=(winner->input+1)%router.inputs.size();
                // This input cannot win a second output in the same cycle.
                const int winning_input=winner->input;
                reqs.erase(std::remove_if(reqs.begin(),reqs.end(),[&](const Request&q){
                    return q.input==winning_input;
                }),reqs.end());
            }
        }
    }

    void sample_state() {
        for(size_t id=0;id<reservations.size();++id) {
            // Match Garnet's advertised q exactly: it is the number of
            // occupied adaptive output VCs on this directed express link,
            // not the short-lived traversal/output queue occupancy.  The
            // latter was only a statistics bug (routing already called
            // express_vcs_occupied()), but it made calibration diagnostics
            // report the wrong signal.
            const uint64_t q=express_vcs_occupied(int(id));
            const uint64_t r=std::max<int64_t>(0,reservations[id]);
            stats.q_sum[id]+=q;stats.q_max[id]=std::max(stats.q_max[id],q);
            stats.r_sum[id]+=r;stats.r_max[id]=std::max(stats.r_max[id],r);
        }
        stats.state_samples++;
    }

    const DirectedLink &express_link(int id) const {
        const int e=id/2;
        const int src=id%2==0?edges[e].u:edges[e].v;
        const int dst=id%2==0?edges[e].v:edges[e].u;
        return links[link_index[src][dst]];
    }

    int express_vcs_occupied(int id) const {
        const auto &busy=express_link(id).busy;
        int occupied=0;
        // The last VC is the escape VC. Escape packets use only the mesh XY
        // subnetwork, so it is excluded from adaptive express pressure.
        for(int vc=0;vc<adaptive_vc_count();++vc)occupied+=busy[vc];
        return occupied;
    }

    uint32_t quantize_express_pressure(uint64_t value) const {
        if(o.express_info_bits==0)
            return uint32_t(std::min<uint64_t>(
                value,std::numeric_limits<uint32_t>::max()));
        if(o.express_info_bits==1)return value?1:0;
        if(o.express_info_bits==2) {
            if(value==0)return 0;
            if(value==1)return 1;
            if(value<=3)return 2;
            return 4;
        }
        const uint32_t maximum=(uint32_t(1)<<o.express_info_bits)-1;
        return uint32_t(std::min<uint64_t>(value,maximum));
    }

    int mesh_distance(int first,int second) const {
        return std::abs(first%o.dimension-second%o.dimension)+
            std::abs(first/o.dimension-second/o.dimension);
    }

    static uint64_t reservation_token(uint64_t packet,size_t stage) {
        if(stage>=256)fail("reservation stage exceeds token encoding");
        if(packet>(std::numeric_limits<uint64_t>::max()>>8))
            fail("packet id exceeds reservation token encoding");
        return (packet<<8)|stage;
    }

    void maybe_erase_reservation_token(uint64_t token) {
        const auto item=reservation_tokens.find(token);
        if(item!=reservation_tokens.end() && item->second.acknowledged &&
           item->second.state==ReservationToken::Done)
            reservation_tokens.erase(item);
    }

    void process_reservation_control() {
        if(o.express_reservation_mode!="registered")return;
        auto &events=reservation_control[cycle%reservation_control.size()];
        for(const auto &event:events) {
            const auto found=reservation_tokens.find(event.token);
            if(found==reservation_tokens.end())
                fail("reservation control event has no token");
            auto &token=found->second;
            const int id=token.directed_id;
            if(event.kind==ReservationControlEvent::Register) {
                if(token.state==ReservationToken::Pending) {
                    reservations[id]++;
                    token.state=ReservationToken::Registered;
                    if(measuring(cycle))stats.reservation_inc[id]++;
                } else if(token.state==ReservationToken::CancelArrived) {
                    token.state=ReservationToken::Done;
                } else fail("duplicate reservation registration");
                const int entry=express_link(id).src;
                // The source stops using its local shadow only when the first
                // periodic advertisement containing this registration can
                // have reached it.  This avoids a blind interval between the
                // ACK and the delayed/periodic r advertisement.
                const uint64_t next_advertisement=cycle+
                    ((o.express_info_period-cycle%o.express_info_period)%
                     o.express_info_period);
                const uint64_t ack_cycle=next_advertisement+
                    o.express_info_delay+mesh_distance(entry,token.source);
                reservation_control[ack_cycle%reservation_control.size()].push_back(
                    ReservationControlEvent{
                        ReservationControlEvent::Acknowledge,event.token});
            } else if(event.kind==ReservationControlEvent::Acknowledge) {
                if(token.acknowledged)
                    fail("duplicate reservation acknowledgement");
                auto &pending=local_unacknowledged[token.source][id];
                if(pending==0)fail("local reservation acknowledgement underflow");
                --pending;
                token.acknowledged=true;
                if(measuring(cycle))stats.registration_acks++;
            } else {
                if(token.state==ReservationToken::Pending) {
                    token.state=ReservationToken::CancelArrived;
                } else if(token.state==ReservationToken::Registered) {
                    if(--reservations[id]<0)
                        fail("registered reservation underflow on cancel");
                    token.state=ReservationToken::Done;
                    if(measuring(cycle))stats.reservation_dec[id]++;
                } else fail("invalid reservation cancellation state");
                if(measuring(cycle))stats.registration_cancels++;
            }
            maybe_erase_reservation_token(event.token);
        }
        events.clear();
    }

    void register_source_route(Packet &p) {
        if(o.express_reservation_mode!="registered") {
            for(int id:p.express_ids) {
                reservations[id]++;
                if(measuring(cycle))stats.reservation_inc[id]++;
            }
            return;
        }
        int current=p.src;
        uint64_t setup_delay=1;
        for(size_t stage=0;stage<p.express_ids.size();++stage) {
            const int id=p.express_ids[stage];
            const auto &link=express_link(id);
            setup_delay+=mesh_distance(current,link.src);
            const uint64_t token_id=reservation_token(p.id,stage);
            ReservationToken token;
            token.source=p.src;
            token.directed_id=id;
            if(!reservation_tokens.emplace(token_id,token).second)
                fail("duplicate reservation token");
            local_unacknowledged[p.src][id]++;
            const uint64_t arrival_cycle=cycle+setup_delay;
            reservation_control[arrival_cycle%reservation_control.size()].push_back(
                ReservationControlEvent{
                    ReservationControlEvent::Register,token_id});
            if(measuring(cycle))stats.registration_messages++;
            setup_delay+=link.latency;
            current=link.dst;
        }
    }

    void traverse_registered_reservation(Packet &p,size_t stage) {
        const int id=p.express_ids.at(stage);
        if(o.express_reservation_mode!="registered") {
            if(--reservations[id]<0)
                fail("reservation underflow on traversal");
            if(measuring(cycle))stats.reservation_dec[id]++;
            return;
        }
        const uint64_t token_id=reservation_token(p.id,stage);
        const auto found=reservation_tokens.find(token_id);
        if(found==reservation_tokens.end())
            fail("express traversal has no reservation token");
        auto &token=found->second;
        if(token.state!=ReservationToken::Registered) {
            if(measuring(cycle))stats.late_registrations++;
            fail("packet reached express link before route setup registered it");
        }
        if(--reservations[id]<0)
            fail("registered reservation underflow on traversal");
        token.state=ReservationToken::Done;
        if(measuring(cycle))stats.reservation_dec[id]++;
        maybe_erase_reservation_token(token_id);
    }

    void cancel_registered_reservation(
        Packet &p,size_t stage,int current_router) {
        const int id=p.express_ids.at(stage);
        if(o.express_reservation_mode!="registered") {
            if(--reservations[id]<0)
                fail("reservation underflow on escape");
            if(measuring(cycle))stats.reservation_dec[id]++;
            return;
        }
        const uint64_t token_id=reservation_token(p.id,stage);
        const auto found=reservation_tokens.find(token_id);
        if(found==reservation_tokens.end())
            fail("reservation cancellation has no token");
        const int entry=express_link(id).src;
        const uint64_t arrival_cycle=cycle+1+
            mesh_distance(current_router,entry);
        reservation_control[arrival_cycle%reservation_control.size()].push_back(
            ReservationControlEvent{
                ReservationControlEvent::Cancel,token_id});
    }

    void update_express_information() {
        if(o.express_info_mode=="instant")return;
        if(cycle%o.express_info_period!=0)return;
        ExpressInfoSnapshot snapshot;
        snapshot.cycle=cycle;
        snapshot.q.resize(reservations.size());
        snapshot.r.resize(reservations.size());
        for(size_t id=0;id<reservations.size();++id) {
            const uint32_t q=quantize_express_pressure(
                express_vcs_occupied(int(id)));
            const uint32_t r=quantize_express_pressure(
                uint64_t(std::max<int64_t>(0,reservations[id])));
            snapshot.q[id]=q;snapshot.r[id]=r;
            if(!originated_valid[id] || originated_q[id]!=q ||
               originated_r[id]!=r) {
                originated_q[id]=q;originated_r[id]=r;
                originated_valid[id]=1;
                if(measuring(cycle))stats.info_updates+=node_count;
            }
        }
        express_info_history.push_back(std::move(snapshot));
        const uint64_t maximum_age=o.express_info_delay+
            2*uint64_t(node_count)+o.express_info_period+4;
        while(!express_info_history.empty() &&
              express_info_history.front().cycle+maximum_age<cycle)
            express_info_history.pop_front();
    }

    std::pair<double,double> observed_express_pressure(int source,int id) {
        const uint64_t true_q=express_vcs_occupied(id);
        const uint64_t true_r=uint64_t(
            std::max<int64_t>(0,reservations[id]));
        uint64_t observed_q=true_q,observed_r=true_r;
        bool known=true;
        if(o.express_info_mode!="instant") {
            uint64_t delay=o.express_info_delay;
            if(o.express_info_mode=="distance-gossip")
                delay+=mesh_distance(express_link(id).src,source);
            known=false;observed_q=0;observed_r=0;
            if(delay<=cycle) {
                const uint64_t target=cycle-delay;
                for(auto item=express_info_history.rbegin();
                    item!=express_info_history.rend();++item) {
                    if(item->cycle<=target) {
                        observed_q=item->q[id];observed_r=item->r[id];
                        known=true;break;
                    }
                }
            }
        }
        const uint64_t local_pending=o.express_reservation_mode=="registered"?
            local_unacknowledged[source][id]:0;
        observed_r+=local_pending;
        if(measuring(cycle)) {
            stats.info_queries++;
            if(!known)stats.info_unknown_queries++;
            stats.info_true_q_sum+=true_q;
            stats.info_observed_q_sum+=observed_q;
            stats.info_true_r_sum+=true_r;
            stats.info_observed_r_sum+=observed_r;
            stats.info_local_pending_sum+=local_pending;
            stats.info_q_abs_error+=true_q>observed_q?
                true_q-observed_q:observed_q-true_q;
            stats.info_r_abs_error+=true_r>observed_r?
                true_r-observed_r:observed_r-true_r;
        }
        return {double(observed_q),double(observed_r)};
    }

    int link_vcs_occupied(int link_id) const {
        int occupied=0;
        for(int vc=0;vc<adaptive_vc_count();++vc)
            occupied+=links[link_id].busy[vc];
        return occupied;
    }

    std::vector<int> dynamic_dijkstra_path(int source,int destination,
                                           bool all_link_pressure) const {
        const double infinity=std::numeric_limits<double>::infinity();
        std::vector<double> dist(node_count,infinity);dist[source]=0.0;
        std::vector<int> previous_link(node_count,-1);
        using Item=std::pair<double,int>;
        std::priority_queue<Item,std::vector<Item>,std::greater<Item>> ready;
        ready.push({0.0,source});
        while(!ready.empty()) {
            const auto [cost,node]=ready.top();ready.pop();
            if(cost!=dist[node])continue;
            if(node==destination)break;
            for(const int link_id:routers[node].outgoing) {
                const auto &link=links[link_id];
                double weight=link.latency;
                if(link.express_id>=0) {
                    const int64_t committed=all_link_pressure ?
                        link_reservations[link_id] : reservations[link.express_id];
                    weight+=o.reservation_weight*committed;
                    weight+=o.express_vc_weight*link_vcs_occupied(link_id);
                } else if(all_link_pressure) {
                    weight+=o.reservation_weight*link_reservations[link_id];
                    weight+=o.express_vc_weight*link_vcs_occupied(link_id);
                }
                const double candidate=cost+weight;
                if(candidate<dist[link.dst]) {
                    dist[link.dst]=candidate;
                    previous_link[link.dst]=link_id;
                    ready.push({candidate,link.dst});
                }
            }
        }
        if(previous_link[destination]<0)
            fail("dynamic Dijkstra could not reach destination");
        std::vector<int> reversed;
        for(int node=destination;node!=source;) {
            const int link_id=previous_link[node];
            if(link_id<0)fail("broken dynamic Dijkstra predecessor chain");
            reversed.push_back(link_id);
            node=links[link_id].src;
        }
        std::reverse(reversed.begin(),reversed.end());
        return reversed;
    }

    int express_waiters(int id) const {
        const auto &link=express_link(id);
        int waiting=0;
        // This is exactly the number of allocated input VCs currently asking
        // the express output arbiter at its source router.
        for(const auto &input:routers[link.src].inputs)
            for(const auto &slot:input.slots)
                waiting+=slot.packet!=kEmpty && slot.outport==link.outport;
        return waiting;
    }

    bool admit_express_route(const Packet &p) const {
        if(o.express_admission_fraction>=1.0)return true;
        if(o.express_admission_fraction<=0.0)return false;
        uint64_t value=p.id^(uint64_t(p.src)<<32)^uint64_t(p.dest);
        value+=0x9e3779b97f4a7c15ULL;
        value=(value^(value>>30))*0xbf58476d1ce4e5b9ULL;
        value=(value^(value>>27))*0x94d049bb133111ebULL;
        value^=value>>31;
        const long double sample=static_cast<long double>(value)/
            static_cast<long double>(std::numeric_limits<uint64_t>::max());
        return sample<o.express_admission_fraction;
    }

    void dump_ni_stall_state(int src,const char *event,uint64_t length) const {
        if(src!=o.debug_stall_source)return;
        const auto &ni=nis[src];
        const auto &input=routers[src].inputs[0];
        std::cerr << "NI_STALL event=" << event << " cycle=" << cycle
                  << " source=" << src << " consecutive=" << length
                  << " pending=" << ni.messages.size()
                  << " ni_busy=";
        for(bool busy:ni.busy)std::cerr << (busy?'1':'0');
        std::cerr << " ni_slots=";
        for(uint64_t packet:ni.slots) {
            if(packet==kEmpty)std::cerr << "- ";
            else std::cerr << packet << ' ';
        }
        std::cerr << " input_rr_vc=" << input.rr_vc << '\n';
        for(int vc=0;vc<int(o.vcs_per_vnet);++vc) {
            const auto &slot=input.slots[vc];
            std::cerr << "  vc=" << vc;
            if(slot.packet==kEmpty) {
                std::cerr << " empty\n";
                continue;
            }
            const auto found=packets.find(slot.packet);
            if(found==packets.end()) {
                std::cerr << " packet=" << slot.packet << " MISSING\n";
                continue;
            }
            const Packet &p=found->second;
            std::cerr << " packet=" << p.id << " dest=" << p.dest
                      << " injected=" << p.injected
                      << " enqueue=" << slot.enqueue
                      << " age=" << (cycle-slot.enqueue)
                      << " escape=" << p.escape
                      << " express_stage=" << p.express_stage << '/'
                      << p.express_ids.size() << " outport=" << slot.outport
                      << " free_vc="
                      << free_vc_for_packet(src,slot.outport,p);
            const auto &busy=slot.outport==0?routers[src].local_busy:
                links[routers[src].outgoing[slot.outport-1]].busy;
            std::cerr << " out_busy=";
            for(bool value:busy)std::cerr << (value?'1':'0');
            if(slot.outport>0) {
                const auto &link=links[routers[src].outgoing[slot.outport-1]];
                std::cerr << " next=" << link.dst
                          << " express_id=" << link.express_id;
            }
            std::cerr << '\n';
        }
        int outport=-1;
        for(const auto &slot:input.slots) {
            if(slot.packet==kEmpty || slot.outport<=0)continue;
            const auto found=packets.find(slot.packet);
            if(found!=packets.end() && found->second.escape) {
                outport=slot.outport;
                break;
            }
        }
        if(outport<=0)return;
        std::vector<uint8_t> seen(links.size(),0);
        int router=src;
        std::cerr << "  escape_dependency_chain:\n";
        for(size_t depth=0;depth<=links.size();++depth) {
            if(outport<=0 || size_t(outport)>routers[router].outgoing.size()) {
                std::cerr << "    invalid outport=" << outport
                          << " router=" << router << '\n';
                break;
            }
            const int link_id=routers[router].outgoing[outport-1];
            const auto &link=links[link_id];
            std::cerr << "    link=" << link_id << ' ' << link.src << "->"
                      << link.dst << " busy_escape="
                      << link.busy[o.vcs_per_vnet-1];
            if(seen[link_id]) {
                std::cerr << " CYCLE\n";
                break;
            }
            seen[link_id]=1;
            const auto &downstream=routers[link.dst].inputs[
                link.inport].slots[o.vcs_per_vnet-1];
            if(downstream.packet==kEmpty) {
                std::cerr << " downstream_vc3=empty\n";
                break;
            }
            const auto found=packets.find(downstream.packet);
            if(found==packets.end()) {
                std::cerr << " downstream_packet=" << downstream.packet
                          << " MISSING\n";
                break;
            }
            const Packet &p=found->second;
            std::cerr << " packet=" << p.id << " dest=" << p.dest
                      << " enqueue=" << downstream.enqueue
                      << " age=" << (cycle-downstream.enqueue)
                      << " escape=" << p.escape
                      << " next_out=" << downstream.outport;
            if(downstream.outport==0) {
                std::cerr << " local_busy_escape="
                          << routers[link.dst].local_busy[
                              o.vcs_per_vnet-1] << '\n';
                break;
            }
            std::cerr << '\n';
            if(cycle-downstream.enqueue>=1000) {
                const auto &port=routers[link.dst].inputs[link.inport];
                std::cerr << "      blocked_input=" << link.inport
                          << " rr_vc=" << port.rr_vc << " slots:";
                for(int vc=0;vc<int(o.vcs_per_vnet);++vc) {
                    const auto &candidate=port.slots[vc];
                    if(candidate.packet==kEmpty) {
                        std::cerr << " vc" << vc << "=-";
                        continue;
                    }
                    const auto candidate_packet=packets.find(candidate.packet);
                    if(candidate_packet==packets.end()) {
                        std::cerr << " vc" << vc << "=MISSING";
                        continue;
                    }
                    std::cerr << " vc" << vc << "=p" << candidate.packet
                              << "/out" << candidate.outport
                              << "/free" << free_vc_for_packet(
                                  link.dst,candidate.outport,
                                  candidate_packet->second)
                              << "/esc" << candidate_packet->second.escape;
                }
                std::cerr << '\n';
                const int next_out=downstream.outport;
                const int rr=next_out==0?routers[link.dst].rr_local_in:
                    routers[link.dst].rr_link_in[next_out-1];
                std::cerr << "      desired_output_rr_input=" << rr
                          << " input_count="
                          << routers[link.dst].inputs.size() << '\n';
            }
            router=link.dst;
            outport=downstream.outport;
        }
    }

    void initialize_source_route(Packet &p) {
        if(!o.source_route)return;
        p.source_routed=true;
        if(p.src==p.dest)return;
        if(measuring(cycle))sample_state();
        if(o.source_route_policy==5 || o.source_route_policy==6) {
            p.planned_links=dynamic_dijkstra_path(
                p.src,p.dest,o.source_route_policy==6);
            for(const int link_id:p.planned_links)
                if(links[link_id].express_id>=0)
                    p.express_ids.push_back(links[link_id].express_id);
        }
        auto &set=candidates[p.src][p.dest];size_t selected=0;
        if(o.source_route_policy==3)selected=random_int<size_t>(0,set.size()-1);
        else if(o.source_route_policy==1 || o.source_route_policy==2 ||
                o.source_route_policy==4) {
            double best=std::numeric_limits<double>::infinity();
            double mesh_cost=std::numeric_limits<double>::infinity();
            size_t mesh_selected=set.size();
            for(size_t i=0;i<set.size();++i){double cost=set[i].latency;
                for(int id:set[i].express_ids){
                    const auto &link=links[link_index[id%2==0?edges[id/2].u:edges[id/2].v]
                        [id%2==0?edges[id/2].v:edges[id/2].u]];
                    if(o.source_route_policy==1 || o.source_route_policy==2)
                        cost+=link.output_ready_cycles.size();
                    if(o.source_route_policy==2)cost+=reservations[id];
                    if(o.source_route_policy==4) {
                        const auto [observed_q,observed_r]=
                            observed_express_pressure(p.src,id);
                        cost+=o.reservation_weight*observed_r;
                        cost+=o.express_vc_weight*observed_q;
                        cost+=o.express_waiter_weight*express_waiters(id);
                    }
                }
                if(set[i].express_ids.empty() && cost<mesh_cost) {
                    mesh_cost=cost;mesh_selected=i;
                }
                if(cost<best){best=cost;selected=i;}
            }
            if(o.source_route_policy==4 &&
               !set[selected].express_ids.empty() && mesh_selected<set.size() &&
               !admit_express_route(p))
                selected=mesh_selected;
        }
        if(o.source_route_policy<5)p.express_ids=set[selected].express_ids;
        if(o.source_mesh_routing=="phase_xy") {
            if(p.express_ids.empty()) {
                const int vc=p.id%3;
                p.phase_vc={{int8_t(vc),int8_t(vc),int8_t(vc)}};
            } else if(p.express_ids.size()==1) {
                static constexpr int pairs[3][2]={{0,1},{0,2},{1,2}};
                const int choice=p.id%3;
                p.phase_vc={{int8_t(pairs[choice][0]),int8_t(pairs[choice][1]),2}};
            } else p.phase_vc={{0,1,2}};
        }
        if(o.source_mesh_routing=="monotonic_xy") {
            if(p.express_ids.empty()) {
                p.phase_vc_low={{0,0,0}};p.phase_vc_high={{2,2,2}};
            } else if(p.express_ids.size()==1) {
                const int id=p.express_ids[0],e=id/2;
                const int entry=id%2==0?edges[e].u:edges[e].v;
                const int exit=id%2==0?edges[e].v:edges[e].u;
                auto manhattan=[&](int a,int b) {
                    return std::abs(a%o.dimension-b%o.dimension)+
                        std::abs(a/o.dimension-b/o.dimension);
                };
                if(manhattan(p.src,entry)>=manhattan(exit,p.dest)) {
                    p.phase_vc_low={{0,2,2}};p.phase_vc_high={{1,2,2}};
                } else {
                    p.phase_vc_low={{0,1,2}};p.phase_vc_high={{0,2,2}};
                }
            } else {
                p.phase_vc_low={{0,1,2}};p.phase_vc_high={{0,1,2}};
            }
        }
        if(measuring(cycle)) {
            stats.planned[std::min<size_t>(2,p.express_ids.size())]++;
            for(int id:p.express_ids)stats.edge_selected[id]++;
        }
        register_source_route(p);
        if(o.source_route_policy==6)
            for(const int link_id:p.planned_links)link_reservations[link_id]++;
    }

    void run_network_interfaces() {
        for(int src=0;src<node_count;++src) {
            Ni &ni=nis[src];
            const bool ready_message=
                !ni.messages.empty() && ni.messages.front().created<=cycle;
            if(ready_message) {
                const int usable=o.no_escape?int(o.vcs_per_vnet):
                    int(o.vcs_per_vnet)-1;
                int selected=-1;
                for(int i=0;i<usable;++i){int vc=(ni.allocator+i)%usable;
                    if(!ni.busy[vc]){selected=vc;ni.allocator=(vc+1)%usable;break;}}
                if(selected<0) {
                    ni.busy_counter=update_busy_streak(ni.busy_counter,true);
                    const uint64_t consecutive=++debug_consecutive_ni_busy[src];
                    if(src==o.debug_stall_source &&
                       (consecutive==o.debug_stall_threshold ||
                        (consecutive>o.debug_stall_threshold &&
                         (consecutive-o.debug_stall_threshold)%
                             o.debug_stall_interval==0)))
                        dump_ni_stall_state(src,"blocked",consecutive);
                    if (ni.busy_counter > max_ni_busy_streak) {
                        max_ni_busy_streak = ni.busy_counter;
                        max_ni_busy_streak_source = src;
                        max_ni_busy_streak_cycle = cycle;
                    }
                    if (cycle < o.warmup + o.measurement)
                        max_ni_busy_streak_before_drain = std::max(
                            max_ni_busy_streak_before_drain, ni.busy_counter);
                    if(ni.busy_counter>o.deadlock_threshold)
                        record_ni_watchdog(src);
                } else {
                    if(debug_consecutive_ni_busy[src]>=o.debug_stall_threshold)
                        dump_ni_stall_state(
                            src,"recovered",debug_consecutive_ni_busy[src]);
                    debug_consecutive_ni_busy[src]=0;
                    ni.busy_counter=update_busy_streak(ni.busy_counter,false);
                    Pending pending=ni.messages.front();ni.messages.pop_front();
                    Packet p; p.id=next_packet++;p.src=src;p.dest=pending.dest;
                    p.created=pending.created-1;p.injected=cycle;
                    initialize_source_route(p);const uint64_t id=p.id;
                    packets.emplace(id,std::move(p));ni.slots[selected]=id;ni.busy[selected]=true;
                    if(measuring(cycle))stats.injected++;
                }
            } else {
                if(debug_consecutive_ni_busy[src]>=o.debug_stall_threshold)
                    dump_ni_stall_state(
                        src,"queue_idle",debug_consecutive_ni_busy[src]);
                debug_consecutive_ni_busy[src]=0;
                ni.busy_counter=update_busy_streak(ni.busy_counter,false);
            }
            if(ni.next_send_cycle>cycle)continue;
            for(int i=0;i<int(o.vcs_per_vnet);++i){
                int vc=(ni.rr+i)%int(o.vcs_per_vnet);
                if(ni.slots[vc]==kEmpty)continue;
                const uint64_t id=ni.slots[vc];
                arrivals[(cycle+2)%arrivals.size()].push_back(
                    Arrival{Arrival::RouterFlit,src,0,vc,id});
                ni.slots[vc]=kEmpty;
                ni.next_send_cycle=cycle+o.packet_flits;
                ni.rr=(vc+1)%int(o.vcs_per_vnet);break;
            }
        }
    }

    void consume_express_output_queues() {
        for(auto &link:links) while(!link.output_ready_cycles.empty() &&
            link.output_ready_cycles.front()<=cycle)link.output_ready_cycles.pop_front();
    }
};

} // namespace

int main(int argc,char **argv) {
    try {
        Options options=parse_options(argc,argv);
        if(options.self_test){
            // Keep this independent of topology files.
            std::mt19937_64 rng(1);
            if(std::uniform_int_distribution<unsigned>(0,1000)(rng)!=134)
                fail("RNG self-test failed");
            uint64_t streak=0;
            streak=update_busy_streak(streak,true);
            streak=update_busy_streak(streak,true);
            streak=update_busy_streak(streak,false);
            streak=update_busy_streak(streak,true);
            if(streak!=1)fail("busy streak continuity self-test failed");
            if(kDefaultRows!=8 || kDefaultVcs!=4)
                fail("default constant self-test failed");
            std::cout<<"standalone NoC self-test passed\n";return 0;
        }
        Simulator simulator(std::move(options));simulator.run();
        // Parse output path again only to avoid exposing Simulator internals.
        Options parsed=parse_options(argc,argv);
        if(parsed.output.empty())simulator.write_result(std::cout);
        else {std::ofstream out(parsed.output);if(!out)fail("cannot open output: "+parsed.output);
            simulator.write_result(out);}
        return 0;
    } catch(const std::exception &e) {
        std::cerr<<"express_noc: "<<e.what()<<'\n';return 2;
    }
}
