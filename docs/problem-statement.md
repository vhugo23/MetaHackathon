University of Houston Hackathon 
Team Sign-Up Sheet

Problem Statement:
Data centers are an integral part of Meta's infrastructure, consisting of servers that host Meta’s services such as Facebook and Instagram and recently AI Training Clusters. Meta's data centers are designed to provide highly available, scalable, and secure infrastructure to support its global user base. These data centers rely on complex network infrastructures to ensure high availability, scalability, and performance which can directly impact user-experience. 

However, monitoring these networks for performance issues, security threats, and operational anomalies is a demanding task due to several challenges.
Scale and Complexity
Operational Anomalies
High-Speed Traffic

Context:
To ensure optimal performance, Meta needs a robust configuration and monitoring system that can detect anomalies, predict potential issues, and provide actionable insights. We can divide the entire problem space into three parts:

Configuration System: Meta’s network comprises devices from several vendors from switches to routers as well as the servers. We cannot be reliant on just one vendor to provide devices for our entire network since that could lead to Supply Chain issues in case of device shortages. Another disadvantage is that if a vendor has some kind of bug lurking in their portfolio of devices, it could bring down our entire fleet leading to service disruption and potential loss to the company. Now that we have established we need devices and services from multiple vendors, we are faced with the problem of configuring all of these devices. Meta’s network has hundreds of thousands of devices and we cannot simply log in to each device individually and configure them based on the vendor. We need an automated system which takes in a standardized configuration file consisting of all the required attributes, builds the required configuration for the device and then pushes the configuration on the device.
Monitoring System: Now that we have pushed configurations to all of these devices and our network is up and running, we need to monitor the network. Hardware failures are abundant and happen all the time. The scale of hardware problems scale with the number of devices in the network as they become that much more likely. Not only hardware, we face Software and configuration problems as well though not as common. Here arises the need to build a system that can actively monitor and alert people about the problems in Meta’s network. This monitoring system also needs to be platform independent similar to the configuration system and needs to monitor all kinds of devices like servers, switches, routers etc. Not only does it need to be platform independent, it also needs to have great observability. By observability, we mean that it needs to monitor a number of attributes for each device such as uptime, memory usage, CPU usage, network configuration and connectivity etc. The more observability the system has, the easier it is to debug if a problem occurs in the network.
Repair Support System: We built a network, pushed configuration and have a monitoring system. It all sounds great but what will we do when something eventually does go down? We need a robust repair support system. We call it a repair support system because while a lot of repairs maybe as simple as the classic “Turn off and turn back on” which can be automated, some repairs might be more complex and can involve part automation, part human interference as well as completely human interference reliant resulting in a plethora of challenges while automating a repair system. We need support from both Software perspective as well as human interference. We also need some kind of automated tool as part of this system which when the monitoring system encounters an error can parse the logs and generate a summary report of what might be going wrong. Depending on the type of error identified and the respective repair steps, the process can then go on to recover the device in an automated manner or generate alerts for a human to fix the issue.

Goals:
The goal of this hackathon is to develop one or more of the above systems that can configure the Data Center network or provide real-time insights into the network, detect anomalies, prevent potential issues or help repair the network when something goes wrong. The solution should be scalable and flexible. You are free to implement any and all of the above depending on your understanding of the problem. Implementing all the systems in such a short time would be quite difficult, focusing on implementing one of the systems is advisable.

Deliverables:
- A working prototype.
- A presentation showcasing the system's features and capabilities.

Evaluation Criteria:
Students can choose one problem they wish to address in this space and build a solution for it. The solution does not have to be fully functional and can be based on logical assumptions.

Total Points: 10
Creativity:  4
Implementation: 3
Presentation: 2
Efficiency/Optimization: 1

## Core Challenges

- Network scale and complexity
- Multi-vendor device configurations
- Operational anomalies
- High-speed telemetry
- Configuration inconsistency
- Slow incident investigation
- Difficulty identifying root causes

## Intended Users

- Network operations engineers
- Site reliability engineers
- Data-center operators
- Network administrators

## Desired Outcome

Provide operators with a unified way to understand network state,
identify anomalies, investigate incidents, and receive actionable
recommendations across devices from multiple vendors.