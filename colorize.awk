# Usage:
#   colorize -v color=(red|yellow|green) -v pattern=REGEX

BEGIN {
    redColorCode     = "\033[0;31m"
    greenColorCode   = "\033[0;32m"
    yellowColorCode  = "\033[0;33m"
    blueColorCode    = "\033[0;34m"
    magentaColorCode = "\033[0;35m"
    cyanColorCode    = "\033[0;36m"
    defaultColorCode = "\033[0m"

    if (color == "red")          colorCode = redColorCode
    else if (color == "green")   colorCode = greenColorCode
    else if (color == "yellow")  colorCode = yellowColorCode
    else if (color == "blue")    colorCode = blueColorCode
    else if (color == "magenta") colorCode = magentaColorCode
    else if (color == "cyan")    colorCode = cyanColorCode
    else                         colorCode = defaultColorCode
}
index ($0, pattern) { printf colorCode }
                    { print $0 defaultColorCode }
