# Workcell Playground

## Task Definition

A "workcell" is an integrated collection of molecular biology lab instruments, able to execute multiple lab workflows in parallel while efficiently managing use of shared resources.

Our task here is to prototype some ideas about how the control software for such a system might be implemented. We will be building a language for defining workflows, a solution for mapping those workflows to discrete machine instructions, and a solution for scheduling those instructions on lab equipment while avoiding conflicts. One goal is to minimize workflow execution time, by making efficient use of shared resources across multiple simultaneous workflows.

## Conceptual Sketch

The workcell consists of "instruments", which operate on "containers" (typically 96-well plates). Each instrument has a set of operations that it can perform. Containers can be moved from instrument to instrument by means of a robotic arm, which is itself considered another instrument.

Workflows are sequences of operations. Multiple workflows can be executed in parallel, as long as they don't collide with regard to instruments or containers.

## Example Instruments and Operations

### Thermocycler

Operations:

- Check status
- Open door
- Close door
- Execute thermocycle schedule (list of durations and temperatures)

### Plate Storage

Operations:

- Check status
- Retrieve plate (presents plate, ready for retrieval by arm)
- Store plate (recieves plate, stores internally)

### Arm

Operations:

- Check status
- Move plate from instrument A to instrument B

### Head Stamper

Operations:

- Check status
- Draw material from plate A (all wells via 96-tip head), add to plate B.

## Design thoughts

Consider a high-level workflow representation, written by a human (or AI agent), which is "compiled" into specific sets of instrument instructions.

Consider a concept of "synchronized blocks of instructions", implemented via semaphores associated with each instrument and container. In order to issue an instruction to an instrument, one must first grab the semaphore. Operations that involve multiple instruments require getting the semaphore on all involved components.

Consider options for efficient scheduling involving careful arrangement of these synchronized blocks.

## Example Test Workflow

### PCR

1. Retrieve sample plate. (96 human DNA samples)
2. Retrieve primer plate. (96 PCR primers in master mix form)
3. Retrieve empty plate.
4. Head stamp some material from sample plate into empty plate, (henceforth called the "PCR plate").
5. Head stamp some material from primer plate into PCR plate.
6. Move PCR plate to thermocycler, execute program.
7. Return sample plate to storage.
8. Return primer plate to storage.
9. Retrieve PCR plate when ready, add to storage.
