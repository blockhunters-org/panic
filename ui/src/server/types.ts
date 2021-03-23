export enum ErrorCode {
    E_430 = 430,
    E_431 = 431,
    E_432 = 432,
    E_433 = 433,
    E_434 = 434,
    E_435 = 435,
    E_436 = 436,
    E_437 = 437,
    E_438 = 438,
    E_439 = 439,
    E_440 = 440,
    E_441 = 441,
    E_442 = 442,
    E_443 = 443,
    E_444 = 444,
    E_445 = 445,
    E_446 = 446,
    E_447 = 447,
    E_448 = 448,
    E_449 = 449,
    E_450 = 450,
    E_451 = 451,
}

export interface Error {
    code: ErrorCode,
    message: string,
}
