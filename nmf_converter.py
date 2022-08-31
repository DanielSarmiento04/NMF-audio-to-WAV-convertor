# Update 201910/09 by @mwittenbols: Fixed two bugs with the code originally retrieved from https://github.com/quarckster/nmf_to_wav
# Bug 1: While looping through data to determine the compression type in get_compression_type() the data struct itself is updated in the loop
# causing the index i to no longer be an absolute index on the original data struct, but rather the updated one, causing the index to shift outside
# of bounds when the chunk size is larger than 22 bytes. In our case we encountered chunksizes 6 times as large as that (132).
#
# Bug 2: The -f parameter in the most recent ffmpeg version as of writing (4.2.1) takes slightly different values for its -f parameter (force format)
# In the original code the -f parameter was actually used incorrectly with a codec value, as opposed to a format value.
# If you perform a "ffmpeg -formats" you see that those values are different from "ffmpeg -codecs"
# So now the -f parameter is fed actual "formats" correpsonding to the original codec list
#
# Bug 3: In combination with Bug 2 added a "null-check" on "compression" to default to "g729" instead of have an empty -f parameter value
#
# Bug 4: We had to consume and extract more packet headers than the original packet type 4 and subtype 0 to avoid getting 0kb or 1kb WAVs as output with no sound.
# So, we added packet_type 4 and sub_type 3, as well as packet_type 5 and sub_type 300. This has been determined by trial and error and may still need improvement
#
''' Python nmf to wav converter. A structure of nmf file was obtained by
Nice Audio Player decompilation. Using a struct module the script finds raw
audio data and push it through a pipe to ffmpeg.
Usage:
python nmf_converter.py path_to_nmf_file
'''
import os
import sys
import struct
import subprocess

__author__ = "Dmitry Misharov"
__credits__ = "Kirill Yagin"
__email__ = "dmitry.misharov@gmail.com"
__version__ = "0.1"


# Map of types compressions of the nmf file and
# ffmpeg decoders
codecs = {
    0: "g729",
    1: "adpcm_g726",
    2: "adpcm_g726",
    3: "alaw",
    7: "pcm_mulaw",
    8: "g729",
    9: "g723_1",
    10: "g723_1",
    19: "adpcm_g722"
}

# Bug 2: The formats according to FFMPEG 4.2 that correpsond to the codecs list above
# Originally in this sample code codecs are used as format parameter, so let's go a head and actually figure out what the input format is to force conversion 
# through a specific FFMPEG codec
formats = {
    0: "g729",
    1: "g726",
    2: "g726",
    3: "alaw",
    7: "mulaw",
    8: "g729",
    9: "g723_1",
    10: "g723_1",
    19: "g722"
}


def get_packet_header(data):
    "Get required information from packet header."
    return {
        "packet_type": struct.unpack("b", data[0:1])[0],
        "packet_subtype": struct.unpack("h", data[1:3])[0],
        "stream_id": struct.unpack("b", data[3:4])[0],
        "start_time": struct.unpack("d", data[4:12])[0],
        "end_time": struct.unpack("d", data[12:20])[0],
        "packet_size": struct.unpack("I", data[20:24])[0],
        "parameters_size": struct.unpack("I", data[24:28])[0]
    }


def get_compression_type(data):
    "Get compression type of the audio chunk."
    for i in range(0, len(data), 22):
        type_id = struct.unpack("h", data[i:i + 2])[0]
        data_size = struct.unpack("i", data[i + 2:i + 6])[0]
        # Bug 1: See how the data struct we are parsing in chuncks actually gets updated IN the loop, causing the index i to get out of bounds 
        # when the chunk size exceeds 22. In the NMFs we are processing we encounter a lot of chunksizes that are 6x as large (132 bytes)
        # without this change that would cause an exception. 
        # Moving the data in a temp struct, as we only need this to determine our return value 
        # data = struct.unpack("16s", data[i + 6:i + 22])[0]
        temp = struct.unpack("16s", data[i + 6:i + 22])[0]
        if type_id == 10:
            # Changed data into temp so that we are not actually tampering with the data we are looping over
            # return get_data_value(data, data_size)
            return get_data_value(temp, data_size)


def get_data_value(data, data_size):
    '''The helper function to get value of the data
    field from parameters header.'''
    fmt = "{}s".format(data_size)
    data_value = struct.unpack(fmt, data[0:data_size])
    if data_value == 0:
        data_value = struct.unpack(fmt, data[8:data_size])
    data_value = struct.unpack("b", data_value[0])
    return data_value[0]


def chunks_generator(path_to_file):
    "A python generator of the raw audio data."
    try:
        with open(path_to_file, "rb") as f:
            data = f.read()
    except IOError:
        sys.exit("No such file")
    packet_header_start = 0
    while True:
        packet_header_end = packet_header_start + 28
        headers = get_packet_header(data[packet_header_start:packet_header_end])
        # Bug 4: We had to add two more packet_types to the equation to avoid getting Okb WAV files in the end. Not entirely sure if
        # packet_types and packet_subtypes were added to the original NMF specs since the creation of the original convertor at https://github.com/quarckster/nmf_to_wav
        # but this code works for all of the NMF files that we have encountered thus far
        # if headers["packet_type"] == 4 and headers["packet_subtype"] == 0:
        if (headers["packet_type"] == 4 and headers["packet_subtype"] == 0) or (headers["packet_type"] == 4 and headers["packet_subtype"] == 3) or (headers["packet_type"] == 5 and headers["packet_subtype"] == 300):
            chunk_start = packet_header_end + headers["parameters_size"]
            chunk_end = (chunk_start + headers["packet_size"] - headers["parameters_size"])
            chunk_length = chunk_end - chunk_start
            fmt = "{}s".format(chunk_length)
            raw_audio_chunk = struct.unpack(fmt, data[chunk_start:chunk_end])
            yield (get_compression_type(data[packet_header_end:packet_header_end +
                   headers["parameters_size"]]),
                   headers["stream_id"],
                   raw_audio_chunk[0])
        packet_header_start += headers["packet_size"] + 28
        if headers["packet_type"] == 7:
            break


def convert_to_wav(path_to_file):
    "Convert raw audio data using ffmpeg and subprocess."
    previous_stream_id = -1
    processes = {}
    for compression, stream_id, raw_audio_chunk in chunks_generator(path_to_file):
        if stream_id != previous_stream_id and not processes.get(stream_id):
            output_file = os.path.splitext(path_to_file)[0] + "_stream{}".format(stream_id) + ".wav"
            # Bug 3: Always default to g729 when compression does not have a value
            format = formats[0]
            if compression != None:
                format = formats[compression]
            processes[stream_id] = subprocess.Popen(
                ("ffmpeg",
                 "-hide_banner",
                 "-y",
                 "-f",
                 # Bug 2: the -f parameter needs to be a format, not a codec! So take the value of the format that corresponds to the codec, rather than the value for  the codec
                 # codecs[compression],
                 format,
                 "-i",
                 "pipe:0",
                 output_file),
                stdin=subprocess.PIPE
            )
            previous_stream_id = stream_id
        processes[stream_id].stdin.write(raw_audio_chunk)
    for key in processes.keys():
        processes[key].stdin.close()
        processes[key].wait()


if __name__ == "__main__":
    path  = 'Antonio Robledo_1_1_7136254231911145804_1_132.nmf'
    convert_to_wav(path)
