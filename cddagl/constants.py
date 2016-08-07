# Logging
MAX_LOG_SIZE = 1024 * 1024
MAX_LOG_FILES = 5

# Build Downloads
BASE_URLS = {
    'Tiles': {
        'x64': ('http://dev.narc.ro/cataclysm/jenkins-latest/'
                'Windows_x64/Tiles/'),
        'x86': ('http://dev.narc.ro/cataclysm/jenkins-latest/Windows/Tiles/')
    },
    'Console': {
        'x64': ('http://dev.narc.ro/cataclysm/jenkins-latest/'
                'Windows_x64/Curses/'),
        'x86': ('http://dev.narc.ro/cataclysm/jenkins-latest/Windows/Curses/')
    }
}
READ_BUFFER_SIZE = 16 * 1024

# File Management
MAX_GAME_DIRECTORIES = 6
SAVES_WARNING_SIZE = 150 * 1024 * 1024
WORLD_FILES = {'worldoptions.json', 'worldoptions.txt', 'master.gsav'}

# CDDA Launcher Stuff
RELEASES_URL = 'https://github.com/remyroy/CDDA-Game-Launcher/releases'
NEW_ISSUE_URL = 'https://github.com/remyroy/CDDA-Game-Launcher/issues/new'
