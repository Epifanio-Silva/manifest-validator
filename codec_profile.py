# codec_profile.py

import sys
import logging

# Uncomment logging for debugging purposes
#logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

def parse_codec(codec_string: str) -> str:
    if not isinstance(codec_string, str):
        return 'Invalid codec string type'
    logging.debug(f"Parsing codec string: {codec_string}")
    parts = codec_string.split('.')

    if codec_string.startswith('avc1'):
        return parse_avc_codec(codec_string)
    elif codec_string.startswith('hvc1') or codec_string.startswith('hev1'):
        return parse_hevc_codec(codec_string)
    elif codec_string.startswith('av01'):
        return parse_av1_codec(codec_string)
    elif codec_string.startswith('mp4v'):
        return parse_mpeg_codec(codec_string)
    elif codec_string.startswith('mp4a'):
        return parse_audio_codec(codec_string)
    elif codec_string.startswith('ac-3') or codec_string.startswith('ec-3'):
        return parse_ac3_codec(codec_string)
    elif codec_string.startswith('dts') or codec_string.startswith('dtsh') or codec_string.startswith('dtse'):
        return parse_dts_codec(codec_string)
    elif codec_string.startswith('dvav') or codec_string.startswith('dav1') or codec_string.startswith('dvhe'):
        return parse_dolby_vision_codec(codec_string)
    elif codec_string.startswith('stpp'):
        return parse_stpp_codec(codec_string)
    else:
        return 'Profile Not Defined'



# --- AVC (H.264) Parser ---
def parse_avc_codec(codec_string: str) -> str:
    parts = codec_string.split('.')

    # RFC 6381 hex format: avc1.PPCCLL  (e.g. avc1.42E01E)
    if len(parts) == 2 and len(parts[1]) == 6:
        profile_level_id_hex = parts[1]
        try:
            profile_level_id = bytes.fromhex(profile_level_id_hex)
            if len(profile_level_id) != 3:
                return 'Invalid AVC codec string'
        except ValueError:
            return 'Invalid AVC codec string'
        profile_idc = profile_level_id[0]
        constraint_set_flags = profile_level_id[1]
        level_idc = profile_level_id[2]
        logging.debug(f"AVC Profile IDC: {profile_idc}, Constraint Flags: {constraint_set_flags}, Level IDC: {level_idc}")
        profile_name = get_avc_profile_name(profile_idc, constraint_set_flags)
        level = get_avc_level(level_idc)
        if profile_name and level:
            return f'AVC {profile_name} Level {level}'
        return 'Unknown AVC codec parameters'

    # Older decimal shorthand: avc1.<profile_idc>.<level_idc>  (e.g. avc1.66.30)
    if len(parts) == 3:
        try:
            profile_idc = int(parts[1])
            level_idc = int(parts[2])
        except ValueError:
            return 'Invalid AVC codec string'
        logging.debug(f"AVC decimal format — Profile IDC: {profile_idc}, Level IDC: {level_idc}")
        profile_name = get_avc_profile_name(profile_idc, 0)
        level = get_avc_level(level_idc)
        if profile_name and level:
            return f'AVC {profile_name} Level {level}'
        return 'Unknown AVC codec parameters'

    return 'Invalid AVC codec string'

def get_avc_profile_name(profile_idc: int, constraint_set_flags: int) -> str:
    profiles = {
        66: 'Baseline',
        77: 'Main',
        88: 'Extended',
        100: 'High',
        110: 'High 10',
        122: 'High 4:2:2',
        244: 'High 4:4:4 Predictive',
        83: 'Scalable Baseline',
        86: 'Scalable High',
        118: 'Multiview High',
        122: 'High 4:2:2',
        128: 'Stereo High',
        134: 'MFC High',
        135: 'MFC Depth High',
        138: 'Multiview Depth High High',
        139: 'Enhanced Multiview Depth High High',
        244: 'High 4:4:4 Intra',
    }
    profile = profiles.get(profile_idc, 'Unknown Profile')
    logging.debug(f"AVC Profile Name: {profile}")
    # Extract constraint flags
    constraint_set1_flag = (constraint_set_flags & 0x40) >> 6
    constraint_set2_flag = (constraint_set_flags & 0x20) >> 5
    constraint_set3_flag = (constraint_set_flags & 0x10) >> 4
    # Add constrained descriptors based on flags
    if profile_idc in [66, 77, 100]:
        if constraint_set1_flag:
            if profile_idc == 66:
                profile = 'Constrained Baseline'
            elif profile_idc == 77:
                profile = 'Constrained Main'
            elif profile_idc == 100:
                profile = 'Constrained High'
            logging.debug(f"AVC Constrained Profile Name: {profile}")
    return profile

def get_avc_level(level_idc: int) -> str:
    level_table = {
        10: '1',
        11: '1.1',
        12: '1.2',
        13: '1.3',
        20: '2',
        21: '2.1',
        22: '2.2',
        30: '3',
        31: '3.1',
        32: '3.2',
        40: '4',
        41: '4.1',
        42: '4.2',
        50: '5',
        51: '5.1',
        52: '5.2',
        60: '6',
        61: '6.1',
        62: '6.2',
    }
    level = level_table.get(level_idc, 'Unknown')
    logging.debug(f"AVC Level: {level}")
    return level


# --- HEVC (H.265) Parser ---
def parse_hevc_codec(codec_string: str) -> str:
    parts = codec_string.split('.')
    if len(parts) < 4:
        return 'Invalid HEVC codec string'

    profile = parts[1]
    logging.debug(f"HEVC Profile: {profile}")

    # Search for the segment starting with 'H' or 'L'
    tier_and_level_part = None
    for part in parts[2:]:
        if part.startswith('H') or part.startswith('L'):
            tier_and_level_part = part
            break
    if not tier_and_level_part:
        logging.debug("HEVC Tier and Level part not found.")
        return 'Invalid HEVC codec string'

    tier = tier_and_level_part[0]
    level_num_str = tier_and_level_part[1:]
    logging.debug(f"HEVC Tier: {tier}, Level Number String: {level_num_str}")

    profile_name = get_hevc_profile_name(profile)
    tier_name = get_hevc_tier(tier)
    level_name = get_hevc_level(level_num_str)

    logging.debug(f"HEVC Profile Name: {profile_name}, Tier Name: {tier_name}, Level Name: {level_name}")

    if profile_name != 'Unknown Profile' and tier_name != 'Unknown Tier' and level_name != 'Unknown Level':
        return f'HEVC {profile_name} {tier_name} Level {level_name}'
    else:
        return 'Unknown HEVC codec parameters'

def get_hevc_profile_name(profile: str) -> str:
    profiles = {
        '1': 'Main',
        '2': 'Main 10',
        '3': 'Main Still Picture',
        '4': 'Main 4:4:4 16',
        '5': 'Main 4:4:4 10',
        '6': 'Scalable Main',
        '7': 'Scalable Main 10',
    }
    profile_name = profiles.get(profile, 'Unknown Profile')
    logging.debug(f"HEVC Profile Name Retrieved: {profile_name}")
    return profile_name

def get_hevc_tier(tier: str) -> str:
    tier_map = {
        'H': 'High Profile',
        'L': 'Main Profile',
    }
    tier_name = tier_map.get(tier, 'Unknown Tier')
    logging.debug(f"HEVC Tier Name Retrieved: {tier_name}")
    return tier_name

def get_hevc_level(level_num_str: str) -> str:
    """
    Converts HEVC level number string (e.g., '93', '120') to level description (e.g., '3.1', '4.0').
    """
    level_map = {
        30: '1.0',
        60: '2.0',
        63: '2.1',
        90: '3.0',
        93: '3.1',
        120: '4.0',
        123: '4.1',
        126: '4.2',
        150: '5.0',
        153: '5.1',
        156: '5.2',
        159: '5.3',
        180: '6.0',
        183: '6.1',
        186: '6.2',
        189: '6.3',
    }
    try:
        level_num = int(level_num_str)
        level = level_map.get(level_num, 'Unknown Level')
        logging.debug(f"HEVC Level Mapped: {level_num_str} -> {level}")
        return level
    except ValueError:
        logging.debug(f"HEVC Level Conversion Failed for: {level_num_str}")
        return 'Unknown Level'


# --- AV1 Parser ---
def parse_av1_codec(codec_string: str) -> str:
    parts = codec_string.split('.')
    if len(parts) < 4:
        return 'Invalid AV1 codec string'

    profile = parts[1]
    level_tier = parts[2]
    bit_depth = parts[3]

    logging.debug(f"AV1 Profile: {profile}, Level_Tier: {level_tier}, Bit Depth: {bit_depth}")

    # Extract Tier and Level from level_tier
    if level_tier[-1] in ['M', 'H']:
        tier = level_tier[-1]
        level_num_str = level_tier[:-1]
    else:
        tier = ''
        level_num_str = level_tier

    logging.debug(f"AV1 Tier: {tier}, Level Number String: {level_num_str}")

    profile_name = get_av1_profile_name(profile)
    tier_name = get_av1_tier(tier)
    level_name = get_av1_level(level_num_str)
    bits = get_av1_bit_depth(bit_depth)

    logging.debug(f"AV1 Profile Name: {profile_name}, Tier Name: {tier_name}, Level Name: {level_name}, Bit Depth: {bits}")

    if profile_name and tier_name and level_name and bits:
        return f'AV1 {profile_name}, Level {level_name}, {tier_name}, {bits}'
    else:
        return 'Unknown AV1 codec parameters'


def get_av1_profile_name(profile: str) -> str:
    profiles = {
        '0': 'Main Profile',
        '1': 'High Profile',
        '2': 'Professional Profile',
    }
    profile_name = profiles.get(profile, 'Unknown Profile')
    logging.debug(f"AV1 Profile Name Retrieved: {profile_name}")
    return profile_name

def get_av1_tier(tier: str) -> str:
    tier_map = {
        'M': 'Main tier',
        'H': 'High tier',
    }
    tier_name = tier_map.get(tier, 'Unknown Tier')
    logging.debug(f"AV1 Tier Name Retrieved: {tier_name}")
    return tier_name


def get_av1_level(level: str) -> str:
    av1_levels = {
        '00': '2.0',
        '01': '2.1',
        '02': '2.2',
        '03': '2.3',
        '04': '3.0',
        '05': '3.1',
        '06': '3.2',
        '07': '3.3',
        '08': '4.0',
        '09': '4.1',
        '10': '4.2',
        '11': '4.3',
        '12': '5.0',
        '13': '5.1',
        '14': '5.2',
        '15': '5.3',
        '16': '6.0',
        '17': '6.1',
        '18': '6.2',
        '19': '6.3',
        '20': '7.0',
        '21': '7.1',
        '22': '7.2',
        '23': '7.3',
        '31': 'Max'
    }
    level_name = av1_levels.get(level, 'Unknown Level')
    logging.debug(f"AV1 Level Retrieved: {level_name}")
    return level_name

def get_av1_bit_depth(bit_depth: str) -> str:
    bits_map = {
        '08': '8 bits',
        '10': '10 bits',
        '12': '12 bits',
    }
    bits = bits_map.get(bit_depth, f'Unknown bit depth ({bit_depth})')
    logging.debug(f"AV1 Bit Depth Retrieved: {bits}")
    return bits



# --- MPEG-4 Video Parser ---
def parse_mpeg_codec(codec_string: str) -> str:
    parts = codec_string.split('.')
    if len(parts) < 3:
        return 'Invalid MPEG-4 codec string'
    profile = parts[1]
    level = parts[2]
    logging.debug(f"MPEG-4 Profile: {profile}, Level: {level}")
    profile_name = get_mpeg_profile_name(profile)
    level_name = get_mpeg_level(level)
    logging.debug(f"MPEG-4 Profile Name: {profile_name}, Level Name: {level_name}")
    if profile_name and level_name:
        return f'MPEG-4 Video {profile_name} Level {level_name}'
    else:
        return 'Unknown MPEG-4 codec parameters'

def get_mpeg_profile_name(profile: str) -> str:
    profiles = {
        '0': 'Simple',
        '1': 'Simple Scalable',
        '2': 'Core',
        '3': 'Main',
        '4': 'Advanced Coding Efficiency (ACE)',
        '5': 'Advanced Simple',
        '6': 'Core Scalable',
        '7': 'Advanced Coding Efficiency (ACE) Scalable',
        '8': 'Advanced Coding Efficiency (ACE) with Spatial Scalability',
        '20': 'Simple',
    }
    profile_name = profiles.get(profile, f'Unknown Profile ({profile})')
    logging.debug(f"MPEG-4 Profile Name Retrieved: {profile_name}")
    return profile_name

def get_mpeg_level(level: str) -> str:
    level_table = {
        '1': '1',
        '2': '2',
        '3': '3',
        '4': '4',
        '5': '5',
        '6': '6',
        '7': '7',
        '8': '8',
        '9': '9',
        '10': '10',
        '11': '11',
        '12': '12',
        '13': '13',
        '14': '14',
        '15': '15',
    }
    level_name = level_table.get(level, 'Unknown Level')
    logging.debug(f"MPEG-4 Level Retrieved: {level_name}")
    return level_name

# --- Dolby Vision Video Parser ---
def parse_dolby_vision_codec(codec_string: str) -> str:
    parts = codec_string.split('.')
    if len(parts) < 2:
        return 'Invalid Dolby Vision codec string'

    dolby_prefix = parts[0]
    profile_id = parts[1]
    level_info = parts[2] if len(parts) > 2 else ''

    logging.debug(f"Dolby Vision Prefix: {dolby_prefix}, Profile ID: {profile_id}, Level Info: {level_info}")

    profile_name = get_dolby_vision_profile_name(dolby_prefix, profile_id)
    level_name = get_dolby_vision_level(profile_id, level_info)

    if profile_name and level_name:
        return f'Dolby Vision {profile_name}, {level_name}'
    else:
        return 'Unknown Dolby Vision codec parameters'

def get_dolby_vision_profile_name(prefix: str, profile_id: str) -> str:
    """
    Maps Dolby Vision prefixes and profile IDs to descriptive profile names.
    """
    profiles = {
        'dvhe': {
            '04': 'Profile 4 (10-bit HEVC)',
            '05': 'Profile 5 (10-bit HEVC)',
            '07': 'Profile 7 (10-bit HEVC)',
            '08': 'Profile 8 (10-bit HEVC)',
            '09': 'Profile 9 (10-bit HEVC)',
        },
        'dvav': {
            '09': 'Profile 9 (8-bit AVC)',
        },
        'dvhl': {
            '20': 'Profile 20 (10-bit HEVC Multiview)',
        }
    }

    prefix_profiles = profiles.get(prefix, {})
    profile = prefix_profiles.get(profile_id, f'Unknown Profile ({profile_id})')
    logging.debug(f"Dolby Vision Profile Name: {profile}")
    return profile

def get_dolby_vision_level(profile_id: str, level_info: str) -> str:
    """
    Maps specific levels based on Dolby Vision profile.
    """
    level_constraints = {
        '04': {'06': 'Level 6', '07': 'Level 7'},
        '05': {'06': 'Level 6', '07': 'Level 7', '09': 'Level 9'},
        '07': {'06': 'Level 6', '07': 'Level 7'},
        '08': {'05': 'Level 5', '09': 'Level 9'},
        '09': {'04': 'Level 4', '05': 'Level 5', '07': 'Level 7'},
        '20': {'10': 'Level 10'},
    }

    profile_levels = level_constraints.get(profile_id, {})
    level = profile_levels.get(level_info, f'Unknown Level ({level_info})')
    logging.debug(f"Dolby Vision Level: {level}")
    return level



# --- Audio Codec Parser ---
def parse_audio_codec(codec_string: str) -> str:
    parts = codec_string.split('.')
    if len(parts) < 2:
        return 'Invalid Audio codec string'
    object_type = parts[1]
    specific = parts[2] if len(parts) > 2 else ''
    logging.debug(f"AAC Object Type: {object_type}, Specific: {specific}")

    # Check for AAC-related object types
    aac_description = get_aac_description(object_type, specific)
    if aac_description:
        return aac_description

    # Check for AC3 or DTS
    if object_type in ['ac-3', 'ec-3']:
        return parse_ac3_codec(codec_string)
    elif object_type.startswith('dts'):
        return parse_dts_codec(codec_string)

    return 'Unknown Audio codec parameters'


def get_aac_description(object_type: str, specific: str) -> str:
    descriptions = {
        '40': 'AAC LC (Low Complexity)',
        '41': 'AAC LC (Low Complexity)',
        '42': 'AAC LC (Low Complexity)',
        '4D': 'AAC Scalable',
        '5A': 'AAC ER (Error Resilient)',
        '64': 'AAC LD (Low Delay)',
    }
    if object_type in descriptions:
        desc = descriptions[object_type]
        if specific:
            try:
                specific_int = int(specific)
                return f'{desc}, Level: {specific_int}'
            except ValueError:
                # If specific is not purely numeric, retain as is
                return f'{desc}, Level: {specific}'
        else:
            return desc
    else:
        return f'Unknown AAC Object Type ({object_type})'

# --- AC3 Audio Parser ---
def parse_ac3_codec(codec_string: str) -> str:
    if 'ac-3' in codec_string:
        return 'AC3'
    elif 'ec-3' in codec_string:
        return 'E-AC3'
    else:
        return 'Unknown AC3 codec type'

# --- DTS Audio Parser ---
def parse_dts_codec(codec_string: str) -> str:
    parts = codec_string.split('.')
    codec_prefix = parts[0]
    config = parts[1] if len(parts) > 1 else ''

    logging.debug(f"DTS Codec Prefix: {codec_prefix}, Config: {config}")

    if codec_prefix == 'dts':
        return parse_dts_basic(config)
    elif codec_prefix == 'dtsh':
        return parse_dts_hd(config)
    elif codec_prefix == 'dtse':
        return parse_dts_express(config)
    else:
        return 'Unknown DTS codec type'

def parse_dts_basic(config: str) -> str:
    return f'DTS Basic, Config: {config}' if config else 'DTS Basic'

def parse_dts_hd(config: str) -> str:
    return f'DTS-HD, Config: {config}' if config else 'DTS-HD'

def parse_dts_express(config: str) -> str:
    return f'DTS Express, Config: {config}' if config else 'DTS Express'

def parse_stpp_codec(codec_string: str) -> str:
    parts = codec_string.split('.')
    logging.debug(f"STPP Codec Parts: {parts}")

    if len(parts) == 1:
        return 'Subtitles (Timed Text)'
    else:
        profile = '.'.join(parts[1:])
        return f'Subtitles (Timed Text), Profile: {profile}'



# --- Main Execution ---
if __name__ == '__main__':
    if len(sys.argv) > 1:
        codec = sys.argv[1]
    else:
        codec = input('Enter codec string: ')
    result = parse_codec(codec)
    print(result)
