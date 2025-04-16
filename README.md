# Scheduler Agent

![Eclipse VOLTTRON 10.0.5rc0](https://img.shields.io/badge/Eclipse%20VOLTTRON-10.0.5rc0-red.svg)
![Python 3.10](https://img.shields.io/badge/python-3.10-blue.svg)
![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)
[![pypi version](https://img.shields.io/pypi/v/volttron-interoperability.svg)](https://pypi.org/project/volttron-interoperability/)

Main branch tests:&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; [![Main Branch Passing?](https://github.com/eclipse-volttron/volttron-interoperability/actions/workflows/run-tests.yml/badge.svg?branch=main)](https://github.com/eclipse-volttron/volttron-interoperability/actions/workflows/run-tests.yml)

Develop branch tests:&nbsp;&nbsp; [![Develop Branch Passing?](https://github.com/eclipse-volttron/volttron-interoperability/actions/workflows/run-tests.yml/badge.svg?branch=develop)](https://github.com/eclipse-volttron/volttron-interoperability/actions/workflows/run-tests.yml)


## Requirements

* python >= 3.10
* volttron >= 10.0 

## Documentation
# Scheduler Agent

The scheduler agent plays a vital role within the VOLTTRON agents’ framework, facilitating efficient energy management and ensuring seamless integration with other service agents. The agent is primarily responsible for scheduling energy storage and grid operations by leveraging forecasted data on demand generation and pricing. It is supported by a modular architecture allowing for incorporating various algorithmic approaches to optimize energy scheduling efficiently.

The scheduler agent operates by interactive with multiple system components, such as Photovoltaic (PV) systems and Energy Storage System (ESS) through the SunSpec Modbus protocol to ensure smooth communication and interoperability.
This flexibility and adherence to standardized communication protocols help align operations and maintain consistency across the energy network. Furthermore, the scheduler agent interfaces with the DER interoperability agent, real-time control agent, and grid information agent. It utilizes the common format provided by these agents, which is converted by the interoperability agent into a standardized format. 
This setup ensures that data models initially expressed in diverse native formats such as IEC 61850, are efficiently translated into a unified command format based on the IEEE 1547 standard.

## Scheduler Configuration

This configuration defines control logic for a simulated Battery Energy Storage System (ESS) using VOLTTRON.

```json
{
  "ess": {
    "class_name": "FakeESS",
    "power_capacity_kw": 100,
    "energy_capacity_kwh": 125,
    "bess_topic": "devices/PNNL/BESS",
    "soc_point": "SoC",
    "power_reading_point": "",
    "actuator_vip": "",
    "power_command_point": ""
  },
  "modes": [
    {
      "name": "ActivePowerResponseName",
      "class_name": "ActivePowerResponse",
      "activation_threshold": 10.0,
      "output_ratio": 1.0,
      "ramp_params": {}
    }
  ],
  "use_cases": [
    {
      "class_name": "PeakLimiting",
      "realtime_power_topic": "devices/SomeLoad/RealPower"
    }
  ],
  "resolution": 5.0,
  "start_time": null
}
```



## Installation

Before installing, VOLTTRON should be installed and running.  Its virtual environment should be active.
Information on how to install of the VOLTTRON platform can be found
[here](https://github.com/eclipse-volttron/volttron-core).

#### Install and start the IEEE 1547.1 Interoperability Agent:

```shell
vctl install scheduler --vip-identity agent.scheduler --tag scheduler --start
```



#### View the status of the installed agent

```shell
vctl status
```

## Development

Please see the following for contributing guidelines [contributing](https://github.com/eclipse-volttron/volttron-core/blob/develop/CONTRIBUTING.md).

Please see the following helpful guide about [developing modular VOLTTRON agents](https://github.com/eclipse-volttron/volttron-core/blob/develop/DEVELOPING_ON_MODULAR.md)

                                                                                                                                                                |

