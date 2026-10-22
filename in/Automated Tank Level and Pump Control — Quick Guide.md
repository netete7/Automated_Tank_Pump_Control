# Automated Tank Level and Pump Control — Quick Guide

## Operating modes

- **Automatic:** Set `AUTO_MODE` to `TRUE`. The pump starts when `LOW_LEVEL` is active and stops at `HIGH_LEVEL`.
- **Manual:** Set `AUTO_MODE` to `FALSE`. Set `MANUAL_PUMP_REQUEST` to `TRUE` to request pumping; set it to `FALSE` to stop.

High-level, high-high-level, sensor-fault, and pump-start-failure protections remain active in either mode.

## Basic checks

1. In automatic mode, activate `LOW_LEVEL` and confirm `PUMP_COMMAND` turns on.
2. Activate `HIGH_LEVEL` and confirm `PUMP_COMMAND` turns off.
3. In manual mode, request pumping and confirm `PUMP_COMMAND` turns on; remove the request and confirm it turns off.
4. In manual mode, test `HIGH_LEVEL`, `HIGH_HIGH_LEVEL`, and `SENSOR_FAULT` one at a time. Confirm each stops the pump.
5. In automatic mode, activate `MANUAL_PUMP_REQUEST` while `LOW_LEVEL` is inactive. Confirm the pump stays off.
6. Return the inputs to normal, healthy values and save the project.

The five-second pump-start failure timer expects `PUMP_RUNNING` feedback. If the simulation does not provide that feedback, the timer may report a start failure.
