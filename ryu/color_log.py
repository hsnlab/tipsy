import logging

# INSTALLATION
# 1. Copy the file to ~ryu/ryu/contrib
# 2. $ pip uninstall ryu
# 3. ~ryu $ pip install .

# https://en.wikipedia.org/wiki/ANSI_escape_code#Colors
colors = {'DEBUG'    : '39;49',
          'INFO'     : '32',
          'WARNING'  : '33',
          'ERROR'    : '31',
          'CRITICAL' : '33;41',
}

class Formatter(logging.Formatter):

    def format(self, record):
        color = colors.get(record.levelname, colors['DEBUG'])
        f = "\033[%sm%%s\033[0m" % color
        record.msg = f % record.msg
        return super(Formatter, self).format(record)
