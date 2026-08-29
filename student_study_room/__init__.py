"""Student AI study-room integration.

The heavy MediaPipe/OpenCV runtime is loaded lazily by ``StudyRoomService`` so
the core teaching system still starts when camera dependencies are absent.
"""

