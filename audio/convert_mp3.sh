#!/bin/bash

# Check if ffmpeg is installed
if ! command -v ffmpeg &> /dev/null; then
    echo "ffmpeg not found, please install ffmpeg"
    exit 1
fi

# Find all MP3 files recursively in current directory and subdirectories
find . -type f -name "*.mp3" | while read -r file; do
    # Get directory path of input file
    dir_path=$(dirname "$file")
    
    # Generate output file name with .ogg extension in same directory
    output_file="${dir_path}/$(basename "${file%.mp3}.ogg")"
    
    # Create output directory if it doesn't exist
    mkdir -p "$dir_path"
    
    # Convert file to ogg format with specified parameters
    ffmpeg -i "$file" -c:a libopus -b:a 32k -vbr on -compression_level 10 -frame_duration 60 -application voip "$output_file"
    
    # Success message
    echo "File '$file' was successfully converted to '$output_file'"
done

# Completion message
echo "All MP3 files have been successfully converted to OGG format"
