#!/usr/bin/bash
set -euo pipefail

# Check for --kill flag first
if [ $# -ge 1 ] && [ "$1" == "--kill" ]; then
    shift
    PORT=1234
    if [ $# -ge 1 ]; then
        PORT="$1"
    fi

    PID_FILE="/tmp/vllm_serve_pids_${PORT}.txt"
    if [ -f "$PID_FILE" ]; then
        echo "Killing vLLM servers (PIDs from $PID_FILE)..."
        while read -r pid; do
            if kill -0 "$pid" 2>/dev/null; then
                echo "Killing process $pid"
                kill "$pid" || true
            else
                echo "Process $pid already dead"
            fi
        done < "$PID_FILE"
        rm -f "$PID_FILE"
        echo "All vLLM servers stopped"
    else
        echo "No PID file found at $PID_FILE"
        echo "No servers to kill"
    fi
    exit 0
fi

if [ $# -lt 1 ]; then
    echo "Usage: $0 <hf_model_name> [--port PORT] [--tensor-parallel-size SIZE] [extra vLLM args...]"
    echo "       $0 --kill [PORT]  # Kill all vLLM servers started with the given port (default: 1234)"
    exit 1
fi

MODEL_NAME="$1"
shift

# Default port and tensor-parallel-size (can be overridden)
PORT=1234
TENSOR_PARALLEL_SIZE=1
EXTRA_ARGS=()

# Simple arg parser for --port and --tensor-parallel-size
while [ $# -gt 0 ]; do
    case "$1" in
        --port)
            if [ $# -lt 2 ]; then
                echo "Error: --port requires a value"
                exit 1
            fi
            PORT="$2"
            shift 2
            ;;
        --tensor-parallel-size)
            if [ $# -lt 2 ]; then
                echo "Error: --tensor-parallel-size requires a value"
                exit 1
            fi
            TENSOR_PARALLEL_SIZE="$2"
            shift 2
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

# Auto-detect AMD GPUs if not manually specified
if command -v rocm-smi &> /dev/null; then
    NUM_GPUS=$(rocm-smi --showid | grep -c "Device Name:")
    if [ $NUM_GPUS -gt 0 ]; then
        DEVICE_IDS=$(seq -s, 0 $((NUM_GPUS-1)))
        echo "Auto-detected $NUM_GPUS AMD GPU(s)"
    else
        DEVICE_IDS="0"
        echo "Warning: no AMD GPUs detected, using device 0"
    fi
else
    DEVICE_IDS="0"
    echo "Warning: rocm-smi not found, using device 0"
fi

GPUS_PER_NODE=$(echo $DEVICE_IDS | tr ',' '\n' | wc -l)

export RAY_EXPERIMENTAL_NOSET_HIP_VISIBLE_DEVICES=1

# Validate that TENSOR_PARALLEL_SIZE divides GPUS_PER_NODE evenly
if [ $((GPUS_PER_NODE % TENSOR_PARALLEL_SIZE)) -ne 0 ]; then
    echo "Error: TENSOR_PARALLEL_SIZE ($TENSOR_PARALLEL_SIZE) must divide GPUS_PER_NODE ($GPUS_PER_NODE) evenly"
    exit 1
fi

# Calculate number of servers (same as old data parallel size)
N_SERVERS=$((GPUS_PER_NODE / TENSOR_PARALLEL_SIZE))

echo "Configuration: GPUS_PER_NODE=$GPUS_PER_NODE, TENSOR_PARALLEL_SIZE=$TENSOR_PARALLEL_SIZE, N_SERVERS=$N_SERVERS"
echo "Starting $N_SERVERS vLLM server(s) on ports $PORT to $((PORT + N_SERVERS - 1))"

# Convert DEVICE_IDS string to array
IFS=',' read -ra GPU_ARRAY <<< "$DEVICE_IDS"

# Launch multiple vLLM servers on different ports
for i in $(seq 0 $((N_SERVERS - 1))); do
    CURRENT_PORT=$((PORT + i))

    # Calculate GPU indices for this server
    GPU_START=$((i * TENSOR_PARALLEL_SIZE))
    GPU_END=$((GPU_START + TENSOR_PARALLEL_SIZE - 1))

    # Build CUDA_VISIBLE_DEVICES or HIP_VISIBLE_DEVICES
    SERVER_GPUS=""
    for gpu_idx in $(seq $GPU_START $GPU_END); do
        if [ -z "$SERVER_GPUS" ]; then
            SERVER_GPUS="${GPU_ARRAY[$gpu_idx]}"
        else
            SERVER_GPUS="$SERVER_GPUS,${GPU_ARRAY[$gpu_idx]}"
        fi
    done

    echo "Starting server $i on port $CURRENT_PORT with GPUs: $SERVER_GPUS"

    # Launch vLLM server in background
    CUDA_VISIBLE_DEVICES="$SERVER_GPUS" OMP_NUM_THREADS=1 vllm serve "$MODEL_NAME" \
        --host 0.0.0.0 \
        --port "$CURRENT_PORT" \
        --tensor-parallel-size "$TENSOR_PARALLEL_SIZE" \
        --gpu-memory-utilization 0.6 \
        "${EXTRA_ARGS[@]}" &
done

# Wait for all background processes
wait
