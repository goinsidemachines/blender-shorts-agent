# Fields

A field is a package under `bsa/fields/<field>/` holding simulations or models, `verify` functions that check
them against sourced numbers, and a playbook chapter at `skills/make-short/playbook/fields/<field>.md`.

Current fields: `physics` (RK4, pendulum, mass-spring-damper, tuned mass damper, a 1-D contact chain with impacts).
SI units throughout.

When no field helper fits, write the model in the short's own `source/model.py`, using `integrate.rk4`
where it helps, and check it with `verify` against a sourced number before animating.
