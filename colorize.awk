# Usage:
#   colorize -v color=(red|yellow|green) -v pattern=REGEX

BEGIN {
    redColorCode = "\033[0;31m"
    greenColorCode = "\033[0;32m"
    yellowColorCode = "\033[0;33m"
    defaultColorCode = "\033[0m"

    if (color == "red") {
        colorCode = redColorCode
    }
    else if (color == "yellow") {
        colorCode = yellowColorCode
    }
    else if (color == "green") {
        colorCode = greenColorCode
    }
    else {
        colorCode = defaultColorCode
    }
}
index ($0, pattern) { printf colorCode }
                    { print $0 defaultColorCode }
