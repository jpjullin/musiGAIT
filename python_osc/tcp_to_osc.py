from pythonosc.osc_server import BlockingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient
from pythonosc.dispatcher import Dispatcher
from enum import Enum
import threading
import datetime
import struct
import socket
import json
import time

# Protocol Version
VERSION = 1

# OSC send to MaxMSP
OSC_IP, OSC_PORT = "127.0.0.1", 8000
osc_client = SimpleUDPClient(OSC_IP, OSC_PORT)
osc_lock = threading.Lock()

# EMG to Delsys server
EMG_HOST, EMG_PORTS = "127.0.0.1", [5123, 5124, 5125, 5126]  # Command, Response, Data, Analyses
CURRENT_SENSORS = []
SOCKETS = []

# OSC to change analyzer configuration
ANALYZER_IP, ANALYZER_PORT = "127.0.0.1", 8001

# Data multiplier
DATA_MULTIPLIER = 10000

# Analyzer configuration
ANALYZER_LEFT_CHANNEL = 13
ANALYZER_LEFT_THRESHOLD = 5

ANALYZER_RIGHT_CHANNEL = 14
ANALYZER_RIGHT_THRESHOLD = 5

ANALYZER_DEVICE = "DelsysEmgDataCollector"
ANALYZER_REFERENCE = "DelsysEmgDataCollector"

analyzer_config_left = {
    "name": "foot_cycle_left",
    "analyzer_type": "cyclic_timed_events",
    "time_reference_device": ANALYZER_REFERENCE,
    "learning_rate": 0.5,
    "initial_phase_durations": [400, 600],
    "events": [
        {
            "name": "heel_strike",
            "previous": "toe_off",
            "start_when": [
                {
                    "type": "threshold",
                    "device": ANALYZER_DEVICE,
                    "channel": ANALYZER_LEFT_CHANNEL - 1,
                    "comparator": ">=",
                    "value": ANALYZER_LEFT_THRESHOLD
                }
            ]
        },
        {
            "name": "toe_off",
            "previous": "heel_strike",
            "start_when": [
                {
                    "type": "threshold",
                    "device": ANALYZER_DEVICE,
                    "channel": ANALYZER_LEFT_CHANNEL - 1,
                    "comparator": "<",
                    "value": ANALYZER_LEFT_THRESHOLD
                }
            ]
        }
    ]
}

analyzer_config_right = {
    "name": "foot_cycle_right",
    "analyzer_type": analyzer_config_left["analyzer_type"],
    "time_reference_device": ANALYZER_REFERENCE,
    "learning_rate": analyzer_config_left["learning_rate"],
    "initial_phase_durations": analyzer_config_left["initial_phase_durations"],
    "events": [
        {
            "name": "heel_strike",
            "previous": "toe_off",
            "start_when": [
                {
                    "type": "threshold",
                    "device": ANALYZER_DEVICE,
                    "channel": ANALYZER_RIGHT_CHANNEL-1,
                    "comparator": ">=",
                    "value": ANALYZER_RIGHT_THRESHOLD
                }
            ]
        },
        {
            "name": "toe_off",
            "previous": "heel_strike",
            "start_when": [
                {
                    "type": "threshold",
                    "device": ANALYZER_DEVICE,
                    "channel": ANALYZER_RIGHT_CHANNEL-1,
                    "comparator": "<",
                    "value": ANALYZER_RIGHT_THRESHOLD
                }
            ]
        }
    ]
}


# Commands
class Command(Enum):
    HANDSHAKE = 0
    CONNECT_DELSYS_ANALOG = 10
    CONNECT_DELSYS_EMG = 11
    CONNECT_MAGSTIM = 12
    ZERO_DELSYS_ANALOG = 40
    ZERO_DELSYS_EMG = 41
    DISCONNECT_DELSYS_ANALOG = 20
    DISCONNECT_DELSYS_EMG = 21
    DISCONNECT_MAGSTIM = 22
    START_RECORDING = 30
    STOP_RECORDING = 31
    GET_LAST_TRIAL_DATA = 32
    ADD_ANALYZER = 50
    REMOVE_ANALYZER = 51
    FAILED = 100


def log_message(message, level="INFO") -> None:
    """Print messages with a timestamp and log level."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] [{level}]: {message}")


def to_packet(command_int: int) -> bytes:
    """
    Create an 8-byte packet: 4 bytes for version + 4 bytes for command.

    Returns:
        bytes: Packed 8-byte packet (little-endian).
    """
    return struct.pack("<II", VERSION, command_int)


def interpret_response(response: bytes) -> dict:
    """
    Interpret a 16-byte response from the server.

    Expected response structure:
    - 4 bytes: Protocol version (little-endian, should be == VERSION)
    - 8 bytes: Timestamp (little-endian, milliseconds since UNIX epoch)
    - 4 bytes: Response code (little-endian, NOK = 0, OK = 1)

    Returns:
        dict: Parsed response with protocol version, timestamp, human-readable time, and status.
    """

    if not response or len(response) != 16:
        return {
            "error": "Invalid response length",
            "raw_data": response.hex() if response else None
        }

    # Unpack response (little-endian format)
    protocol_version, timestamp, status = struct.unpack('<I Q I', response)

    # Check protocol version
    if protocol_version != VERSION:
        return {
            "error": f"Invalid protocol version: {protocol_version}",
            "raw_data": response.hex() if response else None
        }

    # Convert timestamp to human-readable format
    timestamp_seconds = timestamp / 1000.0
    human_readable_time = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(timestamp_seconds))

    return {
        "protocol_version": protocol_version,
        "timestamp": timestamp,
        "human_time": human_readable_time,
        "status": status,
    }


def send_command(sock, command: Command) -> bool:
    """
    Send a command to the server using the required protocol format.

    The command is structured as:
    - 4 bytes (little-endian) for protocol version
    - 4 bytes (little-endian) for the command

    Returns:
        bool: True if the command was successful, False otherwise.
    """
    if not isinstance(command, Command):
        log_message(f"Invalid command {command}", "ERROR")
        return False

    # Pack command as 8 bytes (4-byte version + 4-byte command)
    command_packet = to_packet(command.value)

    try:
        # Send the command
        sock.sendall(command_packet)
        log_message(f"Sent command: {command.name} ({command.value})")

        # Read the full 16-byte response
        response = sock.recv(16)
        parsed_response = interpret_response(response)

        # Handle interpretation errors
        if "error" in parsed_response:
            log_message(f"Response interpretation error: {parsed_response['error']}", "ERROR")
            return False

        # Check if response is OK (1) or NOK (0)
        if parsed_response["status"] != 1:
            log_message(f"Command {command.name} failed", "ERROR")
            return False

        return True

    except socket.error as e:
        log_message(f"Socket error: {e}", "ERROR")
        return False


def send_extra_data(sock, response_sock, extra_data: dict) -> bool:
    """
    Sends extra data in the correct format.

    Format:
    - 4 bytes: Protocol version (little-endian, should be == VERSION)
    - 4 bytes: Length of the JSON string (little-endian)
    - Remaining bytes: The JSON string itself

    Returns:
        bool: True if extra data was sent successfully, False otherwise.
    """

    try:
        # Serialize extra data as JSON
        json_data = json.dumps(extra_data).encode('utf-8')
        data_length = len(json_data)

        # Create the header (VERSION, DATA SIZE)
        header = struct.pack('<II', VERSION, data_length)

        # Send the header and JSON data
        sock.sendall(header + json_data)

        # Wait for the response
        response = response_sock.recv(16)
        parsed_response = interpret_response(response)

        if "error" in parsed_response:
            log_message(f"Error in extra data response: {parsed_response['error']}", "ERROR")
            return False

        if parsed_response["status"] != 1:
            log_message(f"Extra data failed (NOK received)", "ERROR")
            return False

        log_message(f"Extra data sent successfully")
        return True

    except socket.error as e:
        log_message(f"Socket error while sending extra data: {e}", "ERROR")
        return False


def connect_and_handshake(host: str, ports: list[int]) -> list[socket.socket] | bool:
    """
    Connects to all ports and performs a handshake.

    Returns:
        list: List of connected sockets if successful, otherwise False.
    """
    sockets = []

    # Attempt to connect to all ports
    for port in ports:
        try:
            sock = socket.create_connection((host, port))
            sockets.append(sock)
            log_message(f"Connected to {host}:{port}")

        except Exception as e:
            log_message(f"Error connecting to {host}:{port}: {e}", "ERROR")
            for s in sockets:
                s.close()
            return False

    # Perform handshake on the first socket
    if len(sockets) == len(ports):
        handshake_message = to_packet(Command.HANDSHAKE.value)
        sockets[0].sendall(handshake_message)

        response = sockets[0].recv(16)
        parsed_response = interpret_response(response)

        if "error" in parsed_response:
            log_message(f"Handshake error: {parsed_response['error']}", "ERROR")
            return False

    log_message("Handshake successful")
    return sockets


def parse_data_length(data: bytes) -> int:
    """Extract expected data length from header bytes 12-15."""
    return struct.unpack('<I', data[12:16])[0]


def send_osc_message(address: str, value: float) -> None:
    """Thread-safe function to send OSC messages."""
    with osc_lock:
        osc_client.send_message(address, value)


def listen_to_live_data(sock: socket) -> None:
    """Listen for live data packets and send them via OSC."""
    buffer = bytearray()
    expected_length = None
    sent_timestamps = set()

    log_message(f"Sending live data via OSC on {OSC_IP}:{OSC_PORT}")

    while True:
        try:
            data = sock.recv(4096)
            if not data:
                break
            buffer.extend(data)

            # Read header to determine packet length if not set
            if expected_length is None and len(buffer) >= 16:
                expected_length = parse_data_length(buffer[:16])
                buffer = buffer[16:]

            # Process full packets
            while expected_length and len(buffer) >= expected_length:
                raw_data = buffer[:expected_length]
                buffer = buffer[expected_length:]
                expected_length = None

                try:
                    decoded_data = json.loads(raw_data.decode('utf-8'))

                    for key in decoded_data.keys():
                        for entry in decoded_data[key]['data']['data']:
                            timestamp, channels = entry[0], entry[1]

                            if timestamp not in sent_timestamps:
                                sent_timestamps.add(timestamp)

                                for channel in CURRENT_SENSORS:
                                    if channel <= len(channels):
                                        # Send data via OSC
                                        send_osc_message(f'/sensor_{channel}', channels[channel-1] * DATA_MULTIPLIER)

                except json.JSONDecodeError:
                    log_message("JSON decode error in live data. Resetting buffer.", "ERROR")
                    buffer.clear()

        except Exception as e:
            log_message(f"Error processing data: {e}", "ERROR")
            break

    sock.close()
    log_message("Live data connection closed")


def listen_to_live_analyses(sock: socket) -> None:
    """Listen for live analyses packets and send them via OSC."""
    buffer = bytearray()
    expected_length = None

    log_message(f"Sending live analyses via OSC on {OSC_IP}:{OSC_PORT}")

    while True:
        try:
            data = sock.recv(4096)
            if not data:
                break
            buffer.extend(data)

            # Read header to determine packet length if not set
            if expected_length is None and len(buffer) >= 16:
                expected_length = parse_data_length(buffer[:16])
                buffer = buffer[16:]

            # Process full packets
            while expected_length and len(buffer) >= expected_length:
                raw_data = buffer[:expected_length]
                buffer = buffer[expected_length:]
                expected_length = None

                try:
                    decoded_data = json.loads(raw_data.decode('utf-8'))

                    if "data" in decoded_data:
                        for key, analysis in decoded_data["data"].items():
                            if isinstance(analysis, list) and len(analysis) >= 2 and isinstance(analysis[1], list):
                                extracted_data = analysis[1]  # Extract the analysis data

                                # Send data via OSC
                                send_osc_message(f'/{key.replace(" ", "_")}', extracted_data)

                            else:
                                log_message(f"Unexpected format in '{key}' data", "ERROR")

                except json.JSONDecodeError:
                    log_message("JSON decode error in analysis data. Resetting buffer.", "ERROR")
                    buffer.clear()

        except Exception as e:
            log_message(f"Error processing analysis data: {e}", "ERROR")
            break

    sock.close()
    log_message("Live analysis connection closed")


def analyzer_update_channels(address: str, *args):
    """Handles incoming OSC messages to update ANALYZER_CHANNEL."""
    global ANALYZER_LEFT_CHANNEL, ANALYZER_RIGHT_CHANNEL

    try:
        with osc_lock:
            ANALYZER_LEFT_CHANNEL = int(args[0])
            ANALYZER_RIGHT_CHANNEL = int(args[1])

        send_analyzer_config()
        log_message(f"Updated ANALYZER_CHANNELS: {ANALYZER_LEFT_CHANNEL}, {ANALYZER_RIGHT_CHANNEL}")

    except (ValueError, IndexError) as e:
        log_message(f"Error updating ANALYZER_CHANNEL: {e}", "ERROR")


def analyzer_update_thresholds(address: str, *args):
    """Handles incoming OSC messages to update ANALYZER_THRESHOLD."""
    global ANALYZER_LEFT_THRESHOLD, ANALYZER_RIGHT_THRESHOLD

    try:
        with osc_lock:
            ANALYZER_LEFT_THRESHOLD = float(args[0])
            ANALYZER_RIGHT_THRESHOLD = float(args[1])

        send_analyzer_config()
        log_message(f"Updated ANALYZER_THRESHOLDS: {ANALYZER_LEFT_THRESHOLD}, {ANALYZER_RIGHT_THRESHOLD}")

    except (ValueError, IndexError) as e:
        log_message(f"Error updating ANALYZER_THRESHOLD: {e}", "ERROR")


def update_analyzer_config():
    """Update the analyzer configuration."""
    global analyzer_config_left, ANALYZER_LEFT_CHANNEL, ANALYZER_LEFT_THRESHOLD

    with osc_lock:
        analyzer_config_left["events"][0]["start_when"][0]["channel"] = ANALYZER_LEFT_CHANNEL - 1
        analyzer_config_left["events"][0]["start_when"][0]["value"] = ANALYZER_LEFT_THRESHOLD
        analyzer_config_left["events"][1]["start_when"][0]["channel"] = ANALYZER_LEFT_CHANNEL - 1
        analyzer_config_left["events"][1]["start_when"][0]["value"] = ANALYZER_LEFT_THRESHOLD


def send_analyzer_config():
    """Send the updated analyzer configuration to the server."""
    global SOCKETS

    if not SOCKETS or len(SOCKETS) < 2:
        log_message("Sockets not initialized, cannot send analyzer configuration.", "ERROR")
        return

    update_analyzer_config()

    # Remove the existing analyzer
    if not send_command(SOCKETS[0], Command.REMOVE_ANALYZER):
        log_message("Failed to remove existing analyzer. Exiting...", "ERROR")
        return

    # Send the analyzer configuration
    if not send_extra_data(SOCKETS[1], SOCKETS[0], {"analyzer": analyzer_config_left["name"]}):
        log_message("Failed to send analyzer configuration. Exiting...", "ERROR")
        return

    # Send ADD_ANALYZER command
    if not send_command(SOCKETS[0], Command.ADD_ANALYZER):
        log_message("Failed to send ADD_ANALYZER command. Exiting...", "ERROR")
        return

    # Send the analyzer configuration
    if not send_extra_data(SOCKETS[1], SOCKETS[0], analyzer_config_left):
        log_message("Failed to send analyzer configuration. Exiting...", "ERROR")
        return

    log_message("Analyzer configuration updated successfully.")


def change_current_sensors(address: str, *args):
    """Handles incoming OSC messages to change the current sensor."""
    global CURRENT_SENSORS

    try:
        with osc_lock:
            CURRENT_SENSORS = [int(arg) for arg in args]

        log_message(f"Changed current sensors to {CURRENT_SENSORS}")

    except (ValueError, IndexError) as e:
        log_message(f"Error changing current sensor: {e}", "ERROR")


def listen_to_osc_updates():
    """Starts an OSC server to listen for threshold and channel updates from Max/MSP."""
    dispatcher = Dispatcher()
    dispatcher.map("/sensors", change_current_sensors)
    dispatcher.map("/analyzer_channels", analyzer_update_channels)
    dispatcher.map("/analyzer_thresholds", analyzer_update_thresholds)

    server = BlockingOSCUDPServer((ANALYZER_IP, ANALYZER_PORT), dispatcher)
    log_message(f"Listening for OSC updates on port {ANALYZER_PORT}...")
    server.serve_forever()


def main():
    """Main function to establish connections and start data processing."""

    # Establish connections with handshake
    global SOCKETS
    SOCKETS = connect_and_handshake(EMG_HOST, EMG_PORTS)

    if not SOCKETS:
        log_message("Failed to establish all connections. Exiting...", "ERROR")
        return

    # Send CONNECT_DELSYS_EMG command
    if not send_command(SOCKETS[0], Command.CONNECT_DELSYS_EMG):
        log_message("Failed to send CONNECT_DELSYS_EMG command. Exiting...", "ERROR")
        return

    # Start live data listener thread
    data_thread = threading.Thread(target=listen_to_live_data, args=(SOCKETS[2],), daemon=True)
    data_thread.start()

    # Send ADD_ANALYZER command
    if not send_command(SOCKETS[0], Command.ADD_ANALYZER):
        log_message("Failed to send ADD_ANALYZER command. Exiting...", "ERROR")
        return

    # Send the analyzer configuration
    if not send_extra_data(SOCKETS[1], SOCKETS[0], analyzer_config_left):
        log_message("Failed to send analyzer configuration. Exiting...", "ERROR")
        return

    # Start live analyses listener thread
    analyses_thread = threading.Thread(target=listen_to_live_analyses, args=(SOCKETS[3],), daemon=True)
    analyses_thread.start()

    # Start the OSC listener to change analyzer configuration
    analyzer_update_thread = threading.Thread(target=listen_to_osc_updates, daemon=True)
    analyzer_update_thread.start()

    try:
        log_message("Connections established. Running... Press Ctrl+C to exit.")
        threading.Event().wait()
    except KeyboardInterrupt:
        log_message("\nShutting down...")
    finally:
        for sock in SOCKETS:
            sock.close()
        log_message("Connections closed")


if __name__ == "__main__":
    main()
