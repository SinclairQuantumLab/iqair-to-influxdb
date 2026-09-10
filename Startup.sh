#!/bin/bash
# bash script to run the specified python script,
# independent of the PWD this bash script is run from.

# script to run
py_path="./main.py"
py_args=(--settings "./settings.toml" "$@")

# terminal window options
# terminal_title=""
# set_terminal_layout "$terminal_title"
# terminal_columns=140
# terminal_rows=30

# echo "Terminal title = $terminal_title"
# echo "Terminal size = ${terminal_columns}x${terminal_rows}"
# echo

# explicit character encoding for the script and the python script
export LANG=en_US.UTF-8
export LC_ALL=en_US.UTF-8

get_toml_sn() {
    local settings_path="$1"

    if [ ! -f "$settings_path" ]; then
        return 0
    fi

    awk -F= '
    /^[[:space:]]*sn[[:space:]]*=/ {
        value=$2
        sub(/#.*/, "", value)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
        gsub(/^["\047]|["\047]$/, "", value)
        print value
        exit
    }
    ' "$settings_path"
}

set_terminal_layout() {
    local title="$1"

    printf '\033]0;%s\007' "$title"

    if [[ "$terminal_columns" =~ ^[0-9]+$ && "$terminal_rows" =~ ^[0-9]+$ ]]; then
        if [ "$terminal_columns" -gt 0 ] && [ "$terminal_rows" -gt 0 ]; then
            printf '\033[8;%s;%st' "$terminal_rows" "$terminal_columns"
        fi
    fi
}

# move working directory to the project folder
echo ">>> cd to the app directory..."
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
# echo $SCRIPT_DIR
cd "$SCRIPT_DIR"
echo "<<< Working directory set to: ${PWD}"
echo

# set terminal title and size
settings_path="./settings.toml"
if [ ${#py_args[@]} -gt 0 ]; then
    settings_path="${py_args[0]}"
fi

sn=$(get_toml_sn "$settings_path")
if [ -z "$sn" ]; then
    sn="unknown"
fi



# use the local virtual environment created by uv
echo ">>> venv checking..."
venv_python=".venv/bin/python"
if [ ! -x "$venv_python" ]; then
    echo "Cannot find venv python: $venv_python"
    read -p "Press Enter to continue..."
    exit 1
fi
echo "<<< venv ready: $venv_python"
echo
echo

# run the main script
echo ">>> Starting app: $py_path ${py_args[*]}"
echo
exec "$venv_python" "$py_path" "${py_args[@]}"
exit_code=$?

echo
echo "<<< End of the script: $py_path"

if [ $exit_code -ne 0 ]; then
    echo
    read -p "Press Enter to continue..."
fi

exit $exit_code