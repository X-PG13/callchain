const fs = require('fs');

class Calculator {
    constructor(value) {
        this.value = value || 0;
    }

    add(x) {
        this.value = increment(this.value, x);
        return this.value;
    }

    subtract(x) {
        return this.value - x;
    }
}

function increment(a, b) {
    return a + b;
}

const double = (x) => {
    return increment(x, x);
};

async function fetchData(url) {
    const response = await fetch(url);
    return response.json();
}

function main() {
    const calc = new Calculator(10);
    calc.add(5);
    console.log(double(3));
}

const handlers = {
    onAdd: (a, b) => increment(a, b),
    onReset: () => 0,
};

module.exports = { Calculator, increment, double, handlers };
