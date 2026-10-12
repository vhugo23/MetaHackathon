import type { IncidentResponse } from "../api/types";
import type { NetworkNode, NetworkStateResponse } from "../api/networkStateTypes";

export const BASELINE_NETWORK_STATE: NetworkStateResponse = {
  collection_id: "collection-baseline",
  collected_at: "2026-10-07T12:00:00Z",
  retrieved_at: "2026-10-07T12:00:03Z",
  source: {
    collector: "containerlab-frr",
    lab_name: "meta-rne-bgp",
  },
  nodes: [
    {
      node_id: "host-1",
      device_id: null,
      role: "host",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "::1/128"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.3/24", "3fff:172:31:250::3/64", "fe80::42:acff:fe1f:fa03/64"],
        },
        {
          name: "eth1",
          oper_state: "up",
          addresses: ["10.1.1.10/24", "fe80::a8c1:abff:feab:274b/64"],
        },
      ],
      bgp: null,
      routes: null,
      reachability: [
        {
          from_node: "host-1",
          target: "10.1.2.10",
          sent: 3,
          received: 3,
          succeeded: true,
        },
      ],
      unavailable: [],
    },
    {
      node_id: "host-2",
      device_id: null,
      role: "host",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "::1/128"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.5/24", "3fff:172:31:250::5/64", "fe80::42:acff:fe1f:fa05/64"],
        },
        {
          name: "eth1",
          oper_state: "up",
          addresses: ["10.1.2.10/24", "fe80::a8c1:abff:fec7:7df8/64"],
        },
      ],
      bgp: null,
      routes: null,
      reachability: [
        {
          from_node: "host-2",
          target: "10.1.1.10",
          sent: 3,
          received: 3,
          succeeded: true,
        },
      ],
      unavailable: [],
    },
    {
      node_id: "leaf-1",
      device_id: "lab1-leaf-1",
      role: "router",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "10.0.0.11/32", "::1/128"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.4/24", "3fff:172:31:250::4/64", "fe80::42:acff:fe1f:fa04/64"],
        },
        {
          name: "eth3",
          oper_state: "up",
          addresses: ["10.1.1.1/24", "fe80::a8c1:abff:fe53:2c74/64"],
        },
        {
          name: "eth1",
          oper_state: "up",
          addresses: ["10.255.0.1/31", "fe80::a8c1:abff:fed7:5e6b/64"],
        },
        {
          name: "eth2",
          oper_state: "up",
          addresses: ["10.255.0.3/31", "fe80::a8c1:abff:fe7e:2e97/64"],
        },
      ],
      bgp: {
        local_as: 65101,
        router_id: "10.0.0.11",
        established_count: 2,
        neighbors: [
          {
            neighbor_ip: "10.255.0.0",
            remote_as: 65000,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 3,
            uptime_ms: 198000,
          },
          {
            neighbor_ip: "10.255.0.2",
            remote_as: 65000,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 3,
            uptime_ms: 198000,
          },
        ],
      },
      routes: [
        {
          prefix: "0.0.0.0/0",
          protocol: "kernel",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "172.31.250.1",
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "10.0.0.1/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.0",
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.0.0.2/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.2",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.0.0.11/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.11/32",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.12/32",
          protocol: "bgp",
          ecmp_path_count: 2,
          next_hops: [
            {
              ip: "10.255.0.0",
              interface: "eth1",
            },
            {
              ip: "10.255.0.2",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.1.1.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth3",
            },
          ],
        },
        {
          prefix: "10.1.1.1/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth3",
            },
          ],
        },
        {
          prefix: "10.1.2.0/24",
          protocol: "bgp",
          ecmp_path_count: 2,
          next_hops: [
            {
              ip: "10.255.0.0",
              interface: "eth1",
            },
            {
              ip: "10.255.0.2",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.0/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.1/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.2/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.3/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "172.31.250.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "172.31.250.4/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
      ],
      reachability: null,
      unavailable: [],
    },
    {
      node_id: "leaf-2",
      device_id: "lab1-leaf-2",
      role: "router",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "10.0.0.12/32", "::1/128"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.2/24", "3fff:172:31:250::2/64", "fe80::42:acff:fe1f:fa02/64"],
        },
        {
          name: "eth1",
          oper_state: "up",
          addresses: ["10.255.0.5/31", "fe80::a8c1:abff:fe77:b4d1/64"],
        },
        {
          name: "eth3",
          oper_state: "up",
          addresses: ["10.1.2.1/24", "fe80::a8c1:abff:fee9:f5/64"],
        },
        {
          name: "eth2",
          oper_state: "up",
          addresses: ["10.255.0.7/31", "fe80::a8c1:abff:fe3d:cb16/64"],
        },
      ],
      bgp: {
        local_as: 65102,
        router_id: "10.0.0.12",
        established_count: 2,
        neighbors: [
          {
            neighbor_ip: "10.255.0.4",
            remote_as: 65000,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 3,
            uptime_ms: 198000,
          },
          {
            neighbor_ip: "10.255.0.6",
            remote_as: 65000,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 3,
            uptime_ms: 198000,
          },
        ],
      },
      routes: [
        {
          prefix: "0.0.0.0/0",
          protocol: "kernel",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "172.31.250.1",
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "10.0.0.1/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.4",
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.0.0.2/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.6",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.0.0.11/32",
          protocol: "bgp",
          ecmp_path_count: 2,
          next_hops: [
            {
              ip: "10.255.0.4",
              interface: "eth1",
            },
            {
              ip: "10.255.0.6",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.0.0.12/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.12/32",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.1.1.0/24",
          protocol: "bgp",
          ecmp_path_count: 2,
          next_hops: [
            {
              ip: "10.255.0.4",
              interface: "eth1",
            },
            {
              ip: "10.255.0.6",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.1.2.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth3",
            },
          ],
        },
        {
          prefix: "10.1.2.1/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth3",
            },
          ],
        },
        {
          prefix: "10.255.0.4/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.5/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.6/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.7/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "172.31.250.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "172.31.250.2/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
      ],
      reachability: null,
      unavailable: [],
    },
    {
      node_id: "spine-1",
      device_id: "lab1-spine-1",
      role: "router",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "10.0.0.1/32", "::1/128"],
        },
        {
          name: "eth2",
          oper_state: "up",
          addresses: ["10.255.0.4/31", "fe80::a8c1:abff:fe6e:8484/64"],
        },
        {
          name: "eth1",
          oper_state: "up",
          addresses: ["10.255.0.0/31", "fe80::a8c1:abff:fe4f:2866/64"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.7/24", "3fff:172:31:250::7/64", "fe80::42:acff:fe1f:fa07/64"],
        },
      ],
      bgp: {
        local_as: 65000,
        router_id: "10.0.0.1",
        established_count: 2,
        neighbors: [
          {
            neighbor_ip: "10.255.0.1",
            remote_as: 65101,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 2,
            uptime_ms: 197000,
          },
          {
            neighbor_ip: "10.255.0.5",
            remote_as: 65102,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 2,
            uptime_ms: 197000,
          },
        ],
      },
      routes: [
        {
          prefix: "0.0.0.0/0",
          protocol: "kernel",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "172.31.250.1",
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "10.0.0.1/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.1/32",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.11/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.1",
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.0.0.12/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.5",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.1.1.0/24",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.1",
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.1.2.0/24",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.5",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.0/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.0/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.4/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.4/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "172.31.250.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "172.31.250.7/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
      ],
      reachability: null,
      unavailable: [],
    },
    {
      node_id: "spine-2",
      device_id: "lab1-spine-2",
      role: "router",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "10.0.0.2/32", "::1/128"],
        },
        {
          name: "eth2",
          oper_state: "up",
          addresses: ["10.255.0.6/31", "fe80::a8c1:abff:fe95:e603/64"],
        },
        {
          name: "eth1",
          oper_state: "up",
          addresses: ["10.255.0.2/31", "fe80::a8c1:abff:fe29:31ab/64"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.6/24", "3fff:172:31:250::6/64", "fe80::42:acff:fe1f:fa06/64"],
        },
      ],
      bgp: {
        local_as: 65000,
        router_id: "10.0.0.2",
        established_count: 2,
        neighbors: [
          {
            neighbor_ip: "10.255.0.3",
            remote_as: 65101,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 2,
            uptime_ms: 197000,
          },
          {
            neighbor_ip: "10.255.0.7",
            remote_as: 65102,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 2,
            uptime_ms: 197000,
          },
        ],
      },
      routes: [
        {
          prefix: "0.0.0.0/0",
          protocol: "kernel",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "172.31.250.1",
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "10.0.0.2/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.2/32",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.11/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.3",
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.0.0.12/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.7",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.1.1.0/24",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.3",
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.1.2.0/24",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.7",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.2/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.2/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.6/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.6/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "172.31.250.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "172.31.250.6/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
      ],
      reachability: null,
      unavailable: [],
    },
  ],
};

export const DEGRADED_NETWORK_STATE: NetworkStateResponse = {
  collection_id: "collection-degraded",
  collected_at: "2026-10-07T12:01:00Z",
  retrieved_at: "2026-10-07T12:01:03Z",
  source: {
    collector: "containerlab-frr",
    lab_name: "meta-rne-bgp",
  },
  nodes: [
    {
      node_id: "host-1",
      device_id: null,
      role: "host",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "::1/128"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.3/24", "3fff:172:31:250::3/64", "fe80::42:acff:fe1f:fa03/64"],
        },
        {
          name: "eth1",
          oper_state: "up",
          addresses: ["10.1.1.10/24", "fe80::a8c1:abff:feab:274b/64"],
        },
      ],
      bgp: null,
      routes: null,
      reachability: [
        {
          from_node: "host-1",
          target: "10.1.2.10",
          sent: 3,
          received: 3,
          succeeded: true,
        },
      ],
      unavailable: [],
    },
    {
      node_id: "host-2",
      device_id: null,
      role: "host",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "::1/128"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.5/24", "3fff:172:31:250::5/64", "fe80::42:acff:fe1f:fa05/64"],
        },
        {
          name: "eth1",
          oper_state: "up",
          addresses: ["10.1.2.10/24", "fe80::a8c1:abff:fec7:7df8/64"],
        },
      ],
      bgp: null,
      routes: null,
      reachability: [
        {
          from_node: "host-2",
          target: "10.1.1.10",
          sent: 3,
          received: 3,
          succeeded: true,
        },
      ],
      unavailable: [],
    },
    {
      node_id: "leaf-1",
      device_id: "lab1-leaf-1",
      role: "router",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "10.0.0.11/32", "::1/128"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.4/24", "3fff:172:31:250::4/64", "fe80::42:acff:fe1f:fa04/64"],
        },
        {
          name: "eth3",
          oper_state: "up",
          addresses: ["10.1.1.1/24", "fe80::a8c1:abff:fe53:2c74/64"],
        },
        {
          name: "eth1",
          oper_state: "down",
          addresses: ["10.255.0.1/31"],
        },
        {
          name: "eth2",
          oper_state: "up",
          addresses: ["10.255.0.3/31", "fe80::a8c1:abff:fe7e:2e97/64"],
        },
      ],
      bgp: {
        local_as: 65101,
        router_id: "10.0.0.11",
        established_count: 1,
        neighbors: [
          {
            neighbor_ip: "10.255.0.0",
            remote_as: 65000,
            state: "Active",
            raw_state: "Active",
            prefixes_received: null,
            uptime_ms: null,
          },
          {
            neighbor_ip: "10.255.0.2",
            remote_as: 65000,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 3,
            uptime_ms: 692000,
          },
        ],
      },
      routes: [
        {
          prefix: "0.0.0.0/0",
          protocol: "kernel",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "172.31.250.1",
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "10.0.0.2/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.2",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.0.0.11/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.11/32",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.12/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.2",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.1.1.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth3",
            },
          ],
        },
        {
          prefix: "10.1.1.1/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth3",
            },
          ],
        },
        {
          prefix: "10.1.2.0/24",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.2",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.2/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.3/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "172.31.250.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "172.31.250.4/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
      ],
      reachability: null,
      unavailable: [],
    },
    {
      node_id: "leaf-2",
      device_id: "lab1-leaf-2",
      role: "router",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "10.0.0.12/32", "::1/128"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.2/24", "3fff:172:31:250::2/64", "fe80::42:acff:fe1f:fa02/64"],
        },
        {
          name: "eth1",
          oper_state: "up",
          addresses: ["10.255.0.5/31", "fe80::a8c1:abff:fe77:b4d1/64"],
        },
        {
          name: "eth3",
          oper_state: "up",
          addresses: ["10.1.2.1/24", "fe80::a8c1:abff:fee9:f5/64"],
        },
        {
          name: "eth2",
          oper_state: "up",
          addresses: ["10.255.0.7/31", "fe80::a8c1:abff:fe3d:cb16/64"],
        },
      ],
      bgp: {
        local_as: 65102,
        router_id: "10.0.0.12",
        established_count: 2,
        neighbors: [
          {
            neighbor_ip: "10.255.0.4",
            remote_as: 65000,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 1,
            uptime_ms: 692000,
          },
          {
            neighbor_ip: "10.255.0.6",
            remote_as: 65000,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 3,
            uptime_ms: 692000,
          },
        ],
      },
      routes: [
        {
          prefix: "0.0.0.0/0",
          protocol: "kernel",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "172.31.250.1",
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "10.0.0.1/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.4",
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.0.0.2/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.6",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.0.0.11/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.6",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.0.0.12/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.12/32",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.1.1.0/24",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.6",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.1.2.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth3",
            },
          ],
        },
        {
          prefix: "10.1.2.1/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth3",
            },
          ],
        },
        {
          prefix: "10.255.0.4/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.5/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.6/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.7/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "172.31.250.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "172.31.250.2/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
      ],
      reachability: null,
      unavailable: [],
    },
    {
      node_id: "spine-1",
      device_id: "lab1-spine-1",
      role: "router",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "10.0.0.1/32", "::1/128"],
        },
        {
          name: "eth2",
          oper_state: "up",
          addresses: ["10.255.0.4/31", "fe80::a8c1:abff:fe6e:8484/64"],
        },
        {
          name: "eth1",
          oper_state: "down",
          addresses: ["10.255.0.0/31", "fe80::a8c1:abff:fe4f:2866/64"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.7/24", "3fff:172:31:250::7/64", "fe80::42:acff:fe1f:fa07/64"],
        },
      ],
      bgp: {
        local_as: 65000,
        router_id: "10.0.0.1",
        established_count: 1,
        neighbors: [
          {
            neighbor_ip: "10.255.0.1",
            remote_as: 65101,
            state: "Active",
            raw_state: "Active",
            prefixes_received: null,
            uptime_ms: null,
          },
          {
            neighbor_ip: "10.255.0.5",
            remote_as: 65102,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 2,
            uptime_ms: 692000,
          },
        ],
      },
      routes: [
        {
          prefix: "0.0.0.0/0",
          protocol: "kernel",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "172.31.250.1",
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "10.0.0.1/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.1/32",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.12/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.5",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.1.2.0/24",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.5",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.4/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.4/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "172.31.250.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "172.31.250.7/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
      ],
      reachability: null,
      unavailable: [],
    },
    {
      node_id: "spine-2",
      device_id: "lab1-spine-2",
      role: "router",
      interfaces: [
        {
          name: "lo",
          oper_state: "unknown",
          addresses: ["127.0.0.1/8", "10.0.0.2/32", "::1/128"],
        },
        {
          name: "eth2",
          oper_state: "up",
          addresses: ["10.255.0.6/31", "fe80::a8c1:abff:fe95:e603/64"],
        },
        {
          name: "eth1",
          oper_state: "up",
          addresses: ["10.255.0.2/31", "fe80::a8c1:abff:fe29:31ab/64"],
        },
        {
          name: "eth0",
          oper_state: "up",
          addresses: ["172.31.250.6/24", "3fff:172:31:250::6/64", "fe80::42:acff:fe1f:fa06/64"],
        },
      ],
      bgp: {
        local_as: 65000,
        router_id: "10.0.0.2",
        established_count: 2,
        neighbors: [
          {
            neighbor_ip: "10.255.0.3",
            remote_as: 65101,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 2,
            uptime_ms: 693000,
          },
          {
            neighbor_ip: "10.255.0.7",
            remote_as: 65102,
            state: "Established",
            raw_state: "Established",
            prefixes_received: 2,
            uptime_ms: 693000,
          },
        ],
      },
      routes: [
        {
          prefix: "0.0.0.0/0",
          protocol: "kernel",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "172.31.250.1",
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "10.0.0.2/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.2/32",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "lo",
            },
          ],
        },
        {
          prefix: "10.0.0.11/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.3",
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.0.0.12/32",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.7",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.1.1.0/24",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.3",
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.1.2.0/24",
          protocol: "bgp",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: "10.255.0.7",
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.2/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.2/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth1",
            },
          ],
        },
        {
          prefix: "10.255.0.6/31",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "10.255.0.6/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth2",
            },
          ],
        },
        {
          prefix: "172.31.250.0/24",
          protocol: "connected",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
        {
          prefix: "172.31.250.6/32",
          protocol: "local",
          ecmp_path_count: 1,
          next_hops: [
            {
              ip: null,
              interface: "eth0",
            },
          ],
        },
      ],
      reachability: null,
      unavailable: [],
    },
  ],
};

// ---------------------------------------------------------------------------
// Helpers (hand-written; the two constants above are generated verbatim from
// the backend GET /network/state serializer over the captured Lab 1
// fixtures: backend/tests/fixtures/containerlab_frr/{baseline,degraded-active}).
// ---------------------------------------------------------------------------

/** The baseline, re-collected later: the recovered network (same facts, new collection). */
export function recoveredNetworkState(): NetworkStateResponse {
  return {
    ...structuredClone(BASELINE_NETWORK_STATE),
    collection_id: "collection-recovered",
    collected_at: "2026-10-07T12:02:00Z",
    retrieved_at: "2026-10-07T12:02:03Z",
  };
}

export function cloneState(state: NetworkStateResponse): NetworkStateResponse {
  return structuredClone(state);
}

/** Replace one node of a state with a modified copy. */
export function withNode(
  state: NetworkStateResponse,
  nodeId: string,
  change: (node: NetworkNode) => NetworkNode,
): NetworkStateResponse {
  const copy = cloneState(state);
  copy.nodes = copy.nodes.map((node) => (node.node_id === nodeId ? change(node) : node));
  return copy;
}

/** Every router reports its BGP facet as unavailable (nothing is claimed). */
export function allBgpUnavailableState(): NetworkStateResponse {
  const copy = cloneState(BASELINE_NETWORK_STATE);
  copy.collection_id = "collection-unavailable";
  copy.nodes = copy.nodes.map((node) =>
    node.role === "router"
      ? {
          ...node,
          bgp: null,
          unavailable: [{ facet: "bgp", reason: "vtysh command failed" }],
        }
      : node,
  );
  return copy;
}

export function bgpDownIncident(
  deviceId: string,
  neighborIp: string,
  overrides: Partial<IncidentResponse> = {},
): IncidentResponse {
  return {
    incident_id: `incident-${deviceId}-${neighborIp}`,
    fingerprint: `fingerprint-${deviceId}-${neighborIp}`,
    device_id: deviceId,
    source: "ANOMALY",
    rule_ref: "RULE-BGP-DOWN",
    affected_resource: `bgp-neighbor:${neighborIp}`,
    severity: "Critical",
    status: "OPEN",
    evidence: { neighbor_ip: neighborIp, previous_state: "Established", state: "Active" },
    recommendation: "Investigate the BGP session.",
    created_at: "2026-10-07T12:01:00Z",
    last_seen_at: "2026-10-07T12:01:00Z",
    occurrence_count: 1,
    updated_at: "2026-10-07T12:01:00Z",
    resolved_at: null,
    ...overrides,
  };
}

/** The two device-scoped incidents of the proven leaf-1 eth1 failure. */
export function failureIncidents(): IncidentResponse[] {
  return [
    bgpDownIncident("lab1-leaf-1", "10.255.0.0"),
    bgpDownIncident("lab1-spine-1", "10.255.0.1"),
  ];
}

export function resolvedFailureIncidents(): IncidentResponse[] {
  return failureIncidents().map((incident) => ({
    ...incident,
    status: "RESOLVED",
    updated_at: "2026-10-07T12:02:00Z",
    resolved_at: "2026-10-07T12:02:00Z",
  }));
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function notFoundNoSnapshotResponse(): Response {
  return jsonResponse(
    { code: "operational_state_not_found", detail: "No operational state has been ingested." },
    404,
  );
}
