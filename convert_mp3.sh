#!/bin/bash

# Check if ffmpeg is installed
if ! command -v ffmpeg &> /dev/null; then
    echo "ffmpeg not found, please install ffmpeg"
    exit 1
fi

# Set input and output directories
input_dir="audio/orig"
output_dir="audio"

# Create output directory if it doesn't exist
mkdir -p "$output_dir"

# Iterate over all MP3 files in the input directory
for file in "$input_dir"/*.mp3; do
    if [[ -f "$file" ]]; then
        # Generate output file name with .ogg extension
        output_file="$output_dir/$(basename "${file%.mp3}.ogg")"
        
        # Convert file to ogg format with specified parameters
        ffmpeg -i "$file" -c:a libopus -b:a 32k -vbr on -compression_level 10 -frame_duration 60 -application voip "$output_file"
        
        # Success message
        echo "File '$file' was successfully converted to '$output_file'"
    fi
done

# Completion message
echo "All MP3 files have been successfully converted to OGG format in the directory '$output_dir'"
