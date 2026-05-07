"""Tabula wire transport: protobuf schema + Noise-framed client/server.

Real implementations land via #16 (proto), #18 (Noise vendoring), #20 (server),
#27 (client dialer), #31 (keygen), #32 (pinning). Until those merge, this
package exposes minimal stub interfaces so other sub-issues (#29 chat UI)
can be implemented and unit-tested against the contract.
"""
