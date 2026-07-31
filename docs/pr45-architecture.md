# PR 45 architecture boundary

Realtime capture remains WAV-only. Delivery encoding runs after stream shutdown, chunk finalization, WAV concatenation, synchronization correction, mixing, and quality analysis.

This boundary keeps compressed encoders outside audio callbacks and writer threads. The selected format belongs to the recording session and is persisted before capture starts. Recovery reads the session format; sessions created before schema version 4 use WAV.
