#pragma once
#include <Arduino.h>

void web_server_start();

// Push a state update to all connected SSE clients.
// Call whenever posture state changes meaningfully.
void web_server_push_state();
