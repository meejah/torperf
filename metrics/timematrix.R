# Load ggplot library without printing out stupid warnings.
options(warn = -1)
suppressPackageStartupMessages(library("ggplot2"))

# Read in filtered data.
data <- read.csv("filtered.csv", stringsAsFactors = FALSE)

# Remove NA's
data <- na.omit(data)

# Remove "outliers"
data <- data[(data$filesize == "50kb" & data$completemillis < 60000) |
             (data$filesize == "1mb" & data$completemillis < 120000) |
             (data$filesize == "5mb" & data$completemillis < 300000), ]

# Plot a matrix of scatter plots; the first step is to define which data
# we want to plot (here: data) and what to put on x and y axis.
ggplot(data, aes(x = as.POSIXct(started), y = completemillis / 1000)) +

# Draw a point for every observation, but with an alpha value of 1/10 to
# reduce overplotting
geom_point(alpha = 1/10) +

# Draw a matrix of these graphs with different filesizes and different
# guards.
facet_grid(filesize ~ guards, scales = "free_y") +

# Rename y axis.
scale_y_continuous(name = "Completion time in seconds") +

# Rename x axis.
scale_x_datetime(name = "Starting time")

# Save the result to a large PNG file.
ggsave("timematrix.png", width = 10, height = 10, dpi = 150)

