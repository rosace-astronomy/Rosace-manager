/**
 * Minimal FITS parser -- single-HDU 2D images only (NAXIS=2), no multi-extension
 * or table support. Enough to read the science/guide images Rosace itself writes.
 */

const BLOCK_SIZE = 2880;
const CARD_SIZE = 80;
const CARDS_PER_BLOCK = BLOCK_SIZE / CARD_SIZE;

export interface FitsImage {
	width: number;
	height: number;
	bitpix: number;
	/** Physical pixel values (BZERO/BSCALE already applied), row 0 = bottom row (FITS convention). */
	data: Float32Array;
	min: number;
	max: number;
	header: Record<string, string>;
}

function parseHeader( buffer: ArrayBuffer ): { header: Record<string, string>, dataOffset: number } {
	const header: Record<string, string> = {};
	const decoder = new TextDecoder();

	let offset = 0;
	let done = false;

	while( !done ) {
		if( offset + BLOCK_SIZE > buffer.byteLength ) {
			throw new Error( "Truncated FITS header (no END card found)" );
		}

		for( let card = 0; card < CARDS_PER_BLOCK; card++ ) {
			const line = decoder.decode( new Uint8Array( buffer, offset, CARD_SIZE ) );
			offset += CARD_SIZE;

			const keyword = line.slice( 0, 8 ).trim();
			if( keyword === "END" ) {
				done = true;
				break;
			}

			// blank/comment/history cards have no "= value" part -- skip them
			if( !keyword || line[8] !== "=" ) {
				continue;
			}

			let value = line.slice( 9 ).trim();
			if( value.startsWith( "'" ) ) {
				// quoted string value -- take up to the closing quote
				value = value.slice( 1, value.indexOf( "'", 1 ) );
			}
			else {
				// numeric/boolean value -- drop a trailing "/ comment"
				const slash = value.indexOf( "/" );
				if( slash >= 0 ) {
					value = value.slice( 0, slash );
				}
				value = value.trim();
			}

			header[keyword] = value;
		}
	}

	// header always occupies a whole number of 2880-byte blocks
	const dataOffset = Math.ceil( offset / BLOCK_SIZE ) * BLOCK_SIZE;
	return { header, dataOffset };
}

export function parseFits( buffer: ArrayBuffer ): FitsImage {
	const { header, dataOffset } = parseHeader( buffer );

	const width  = parseInt( header["NAXIS1"] );
	const height = parseInt( header["NAXIS2"] );
	const bitpix = parseInt( header["BITPIX"] );
	const bzero  = header["BZERO"]  !== undefined ? parseFloat( header["BZERO"] )  : 0;
	const bscale = header["BSCALE"] !== undefined ? parseFloat( header["BSCALE"] ) : 1;

	if( !width || !height ) {
		throw new Error( "Not a 2D FITS image (missing/invalid NAXIS1/NAXIS2)" );
	}

	const n = width * height;
	const data = new Float32Array( n );
	const view = new DataView( buffer, dataOffset );

	let min = Infinity;
	let max = -Infinity;

	for( let i = 0; i < n; i++ ) {
		let raw: number;
		switch( bitpix ) {
			case 8:   raw = view.getUint8( i );              break;
			case 16:  raw = view.getInt16( i * 2, false );   break;
			case 32:  raw = view.getInt32( i * 4, false );   break;
			case -32: raw = view.getFloat32( i * 4, false ); break;
			case -64: raw = view.getFloat64( i * 8, false ); break;
			default:
				throw new Error( `Unsupported BITPIX ${bitpix}` );
		}

		const v = raw * bscale + bzero;
		data[i] = v;
		if( v < min ) min = v;
		if( v > max ) max = v;
	}

	return { width, height, bitpix, data, min, max, header };
}
