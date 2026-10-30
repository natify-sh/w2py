/* A tiny bytecode VM: the dense switch is what compilers turn into br_table. */
typedef unsigned int u32;
static unsigned char prog[256];
static u32 regs[8];

static void init(void) {
    u32 s = 12345;
    for (int i = 0; i < 256; i++) { s = s * 1103515245u + 12345u; prog[i] = (s >> 16) % 32; }
    for (int i = 0; i < 8; i++) regs[i] = i * 7 + 1;
}

__attribute__((export_name("run")))
u32 run(u32 iters) {
    init();
    u32 acc = 0;
    for (u32 n = 0; n < iters; n++) {
        for (u32 pc = 0; pc < 256; pc++) {
            u32 op = prog[pc];
            u32 *r = &regs[pc & 7];
            switch (op) {
            case 0:  acc += *r; break;
            case 1:  acc -= *r; break;
            case 2:  acc ^= *r; break;
            case 3:  acc = (acc << 1) | (acc >> 31); break;
            case 4:  *r += acc; break;
            case 5:  *r ^= acc >> 3; break;
            case 6:  acc *= 3; break;
            case 7:  acc += 0x9e3779b9u; break;
            case 8:  *r = (*r << 3) ^ acc; break;
            case 9:  acc = acc + (*r >> 2); break;
            case 10: acc |= 1; break;
            case 11: acc &= 0x7fffffffu; break;
            case 12: *r -= 17; break;
            case 13: acc ^= 0x55555555u; break;
            case 14: acc += pc; break;
            case 15: *r = *r * 5 + 1; break;
            case 16: acc -= pc * 3; break;
            case 17: acc = ~acc; break;
            case 18: *r ^= *r >> 7; break;
            case 19: acc += *r * 9; break;
            case 20: acc = (acc >> 5) | (acc << 27); break;
            case 21: *r += 0x1234; break;
            case 22: acc ^= *r << 1; break;
            case 23: acc -= 0x77; break;
            case 24: *r = acc - *r; break;
            case 25: acc += acc >> 9; break;
            case 26: *r |= 0x100; break;
            case 27: acc *= 0x01000193u; break;
            case 28: *r &= 0xffff0fffu; break;
            case 29: acc ^= pc << 4; break;
            case 30: *r = *r + (acc & 0xff); break;
            default: acc += 31; break;
            }
        }
    }
    return acc;
}
