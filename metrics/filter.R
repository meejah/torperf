## A new and "improved" genericised version of the old filter script
## This version was created for task 2563
## See HOWTO to put this in context
##
## usage: R -f filter.R --args [-start=DATE] [-end=DATE] FILENAME(S)
## filename must be of the form guardname-basesizeSUFFIX.data
## where SUFFIX is one of kb, mb, gb, tb
##
## eg: R -f filter.R --args -start=2011-02-01 -end=2099-12-31 *.data
## eg: R -f filter.R --args torperf-50kb.data
##
## This R script reads in Torperf files as specified on the command line
## and writes a filtered version to filtered.csv for later processing.

FilterMain <- function(ARGV) {
  kDebug <- FALSE # set TRUE for debugging output
  kVersion <- 0.3
  if (kDebug) { cat("filter.R version ", kVersion, "\n\n") }
  files <- NULL # files is a list of torperfFiles as definied below
  setClass("torperfFile",
           representation(
                          filename = "character",
                          guardLabel = "character",
                          filesizeLabel = "character",
                          filesize = "numeric"
                          )
           )

  ## default values
  ## cutoff dates for observations
  start <- as.POSIXct("2011-02-01", origin = "1970-01-01")
  end   <- as.POSIXct("2099-12-31", origin = "1970-01-01")

  ## process command line arguments
  args <- unlist(strsplit(ARGV, " "))

  ## there are better ways to process command line args, but this works for me :-)
  for (arg in args) {
    if (kDebug) { cat('arg: ', arg, "\n") }
    ## if start date specified
    if (length(splitArgL <- unlist(strsplit(arg, "-start="))) == 2) {
      if (kDebug) { cat('Starting from ', splitArgL[2], '\n') }
      start <- as.POSIXct(splitArgL[2], origin = "1970-01-01")
      next
    }
    ## if end date specified
    if (length(splitArgL <- unlist(strsplit(arg, "-end="))) == 2) {
      if (kDebug) { cat('Ending at ', splitArgL[2], '\n') }
      end <- as.POSIXct(splitArgL[2], origin = "1970-01-01")
      next
    }
    ## if the argument is -start= or -end= we will not reach this line
    ## now, if it isn't a parameter add it to the file list
    ## parse filename for metadata...
    ## examples:
    ##   "torperf-50kb.data" should result in
    ##     filename = "torperf-50kb.data"
    ##     guardLabel = "torperf"
    ##     filesizeLabel = "50kb"
    ##     filesize = 50 * 1024
    my.file <- new("torperfFile", filename = arg)

    ## get base filename (strip out leading parts of filename such as dirname)
    baseFilename <- basename(my.file@filename)
    parseFileStr <- unlist(strsplit(baseFilename, "-")) ## split the two parts of the filename string
    if (length(parseFileStr) != 2) {
      cat("error: filenames must be of the form guard-filesize.data, you said \"", baseFilename, "\"\n")
      quit("no", 1)
    }
    my.file@guardLabel <- parseFileStr[1]
    cdr <- parseFileStr[2]
    parseFilesize <- unlist(strsplit(cdr, "\\."))
    if (length(parseFilesize) != 2) {
      cat("error: tail of filename must be filesize.data, you said \"", cdr, "\"\n")
      quit("no", 1)
    }
    my.file@filesizeLabel <- tolower(parseFilesize[1]) ## smash case to make our life easier

    fileBaseSize <- as.integer(unlist(strsplit(my.file@filesizeLabel, "[a-z]"))[1])
    fileSizeMultiplierStr <- unlist(strsplit(my.file@filesizeLabel, '[0-9]'))
    fileSizeMultiplierStr <- fileSizeMultiplierStr[length(fileSizeMultiplierStr)]
    fileSizeMultiplier <- 1 ## assume no suffix
    if (fileSizeMultiplierStr == "kb") { fileSizeMultiplier <- 1024 }
    if (fileSizeMultiplierStr == "mb") { fileSizeMultiplier <- 1024 * 1024 }
    if (fileSizeMultiplierStr == "gb") { fileSizeMultiplier <- 1024 * 1024 * 1024}
    ## yeah right, like we are really pushing TB of data
    if (fileSizeMultiplierStr == "tb") { fileSizeMultiplier <- 1024 * 1024 * 1024 * 1024 }
    my.file@filesize <- fileBaseSize * fileSizeMultiplier

    if (kDebug) {
      cat("i will read file: ", my.file@filename, ' ',
        my.file@guardLabel, ' ',
        my.file@filesizeLabel, ' ',
        my.file@filesize, "\n")
    }

    files <- c(files, my.file)
  }

  ## sanity check arguments
  if (start >= end) {
    cat("error: start date must be before end date\n");
    quit("no", 1)
  }
  if (length(files) == 0) {
    cat("error: input files must be specified as arguments\n")
    quit("no", 1) ## terminate with non-zero errlev
  }

  if (kDebug) {
    cat("filtering from ", as.character.POSIXt(start), " to ",
        as.character.POSIXt(end), "\n")
  }

  ## Turn a given Torperf file into a data frame with the information we care
  ## about.
  read <- function(filename, guards, filesize, bytes) {
    x <- read.table(filename)
    x <- x[as.POSIXct(x$V1, origin = "1970-01-01") >= start &
           as.POSIXct(x$V1, origin = "1970-01-01") <= end, ]
    if (length(x$V1) == 0)
        NULL
    else
        data.frame(
                   started = as.POSIXct(x$V1, origin = "1970-01-01"),
                   timeout = x$V17 == 0,
                   failure = x$V17 > 0 & x$V20 < bytes,
                   completemillis = ifelse(x$V17 > 0 & x$V20 >= bytes,
                     round((x$V17 * 1000 + x$V18 / 1000) -
                           (x$V1  * 1000 + x$V19 / 1000), 0), NA),
                   guards = guards,
                   filesize = filesize)
  }

  ## Read in files and bind them to a single data frame.
  filtered <- NULL
  for (file in files) {
    if (kDebug) { cat('Processing ', file@filename, "...\n") }
    filtered <- rbind(filtered,
                      read(file@filename, file@guardLabel, file@filesizeLabel, file@filesize)
                      )
  }

                                        # Write data frame to a csv file for later processing.
  write.csv(filtered, "filtered.csv", quote = FALSE, row.names = FALSE)

}

FilterMain(commandArgs(TRUE))
