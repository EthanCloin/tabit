"""voice-vault: transcribe recordings locally and synthesize cross-linked notes.

The pipeline is four stages — capture, transcribe, synthesize, evolve — orchestrated by
:mod:`voicevault.run`. Behavior is steered by user-owned markdown control files in ``config/``
(dictionary, taxonomy, synthesis-guide, feedback, examples), not by editing this package.
"""

__version__ = "0.1.0"
