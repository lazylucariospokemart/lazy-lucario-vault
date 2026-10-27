Drop files here and the robots pick them up.

**Scanner CSV exports** (`*.csv`): the sync adds any new cards to the Owner desk, then moves the file to `done/`.

**Scanner pictures** (`<tcgplayer id>.jpg` or `.png`, e.g. `42367.jpg` or `42367_front.png`): GitHub resizes the picture to 437px tall, saves it as `img/<id>.jpg` so it shows on the storefront, then moves the original to `done/`. If `img/<id>.jpg` already exists it is left alone. Cards that have an id but no picture get one downloaded from TCGplayer automatically, so you only need to drop a picture in when the download didn't find one or you want your own.

Nothing in here is ever deleted.
