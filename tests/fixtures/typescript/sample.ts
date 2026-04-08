import { EventEmitter } from 'events';

interface Logger {
    log(msg: string): void;
}

class Calculator implements Logger {
    private value: number;

    constructor(value: number = 0) {
        this.value = value;
    }

    add(x: number): number {
        this.value = increment(this.value, x);
        return this.value;
    }

    log(msg: string): void {
        console.log(`[Calculator] ${msg}`);
    }

    async fetchValue(url: string): Promise<number> {
        const res = await fetch(url);
        return res.json();
    }
}

function increment(a: number, b: number): number {
    return a + b;
}

const double = (x: number): number => {
    return increment(x, x);
};

const handlers = {
    onAdd: (a: number, b: number) => increment(a, b),
    onReset: () => 0,
};

export { Calculator, increment, double };
