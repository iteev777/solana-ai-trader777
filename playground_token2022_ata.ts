/**
 * Solana Playground Devnet E2E: Token-2022 ATA creation.
 *
 * Run this file inside beta.solpg.io with the user's Playground wallet selected
 * and the cluster set to Devnet. The wallet signs in the browser; no secret key
 * is exported, copied, logged, or sent to GitHub.
 */
import {
  PublicKey,
  Transaction,
  TransactionInstruction,
} from "@solana/web3.js";

const DEVNET_GENESIS_HASH =
  "EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG";
const EXPECTED_PLAYGROUND_WALLET =
  new PublicKey("DrXL17U7RS2Nx6KVTUStpqhcsmDod3RfMS7RnTTSgTRH");
const MINT = new PublicKey("CFTBSCy68VqUzGeMwjmgzufeBgGi64i281f4iUxzavj7");
const TOKEN_2022_PROGRAM = new PublicKey(
  "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
);
const ASSOCIATED_TOKEN_PROGRAM = new PublicKey(
  "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
);
const SYSTEM_PROGRAM = new PublicKey("11111111111111111111111111111111");
const EXPECTED_MINT_SIZE = 278;
const EXPECTED_ATA_SIZE = 182;

async function main() {
  const connection = pg.connection;
  const payer = pg.wallet.publicKey;

  if (!payer || !payer.equals(EXPECTED_PLAYGROUND_WALLET)) {
    throw new Error(
      "Wrong Playground wallet. Select the expected wallet ending in TTSgTRH before running.",
    );
  }

  const genesisHash = await connection.getGenesisHash();
  if (genesisHash !== DEVNET_GENESIS_HASH) {
    throw new Error("Wrong cluster: switch Solana Playground to Devnet.");
  }

  const balance = await connection.getBalance(payer, "confirmed");
  console.log("Playground wallet:", payer.toBase58());
  console.log("Devnet SOL balance:", balance / 1_000_000_000);
  if (balance <= 0) throw new Error("Playground wallet has no Devnet SOL.");

  const mintInfo = await connection.getAccountInfo(MINT, "confirmed");
  if (!mintInfo) throw new Error("Configured Token-2022 mint was not found.");
  if (!mintInfo.owner.equals(TOKEN_2022_PROGRAM)) {
    throw new Error("Configured mint is not owned by Token-2022.");
  }
  if (mintInfo.data.length !== EXPECTED_MINT_SIZE) {
    throw new Error("Mint data size changed; stop and review before proceeding.");
  }

  const [ata, bump] = PublicKey.findProgramAddressSync(
    [payer.toBuffer(), TOKEN_2022_PROGRAM.toBuffer(), MINT.toBuffer()],
    ASSOCIATED_TOKEN_PROGRAM,
  );

  const existingAta = await connection.getAccountInfo(ata, "confirmed");
  if (existingAta) {
    if (!existingAta.owner.equals(TOKEN_2022_PROGRAM)) {
      throw new Error("The derived ATA exists but has the wrong owner program.");
    }
    if (existingAta.data.length !== EXPECTED_ATA_SIZE) {
      throw new Error("Existing ATA has an unexpected data size.");
    }
    console.log({
      result: "PASS_ALREADY_EXISTS",
      cluster: "devnet",
      wallet: payer.toBase58(),
      mint: MINT.toBase58(),
      ata: ata.toBase58(),
      ataSize: existingAta.data.length,
      note: "No transaction sent; this wallet's ATA already exists.",
      liveTrading: "LOCKED",
      mainnetAccess: "BLOCKED",
    });
    return;
  }

  const rentLamports =
    await connection.getMinimumBalanceForRentExemption(EXPECTED_ATA_SIZE);
  // Keep a conservative fee margin in addition to the exact ATA rent.
  if (balance < rentLamports + 100_000) {
    throw new Error(
      "Insufficient Devnet SOL for ATA rent and transaction fee. No transaction sent.",
    );
  }

  const latest = await connection.getLatestBlockhash("confirmed");
  const instruction = new TransactionInstruction({
    programId: ASSOCIATED_TOKEN_PROGRAM,
    keys: [
      { pubkey: payer, isSigner: true, isWritable: true },
      { pubkey: ata, isSigner: false, isWritable: true },
      { pubkey: payer, isSigner: false, isWritable: false },
      { pubkey: MINT, isSigner: false, isWritable: false },
      { pubkey: SYSTEM_PROGRAM, isSigner: false, isWritable: false },
      { pubkey: TOKEN_2022_PROGRAM, isSigner: false, isWritable: false },
    ],
    data: Buffer.from([1]), // Associated Token Program CreateIdempotent
  });

  const tx = new Transaction({
    feePayer: payer,
    recentBlockhash: latest.blockhash,
  }).add(instruction);

  console.log("Requesting signature from the Solana Playground wallet...");
  const signature = await pg.wallet.signAndSendTransaction(tx);
  console.log("Submitted transaction:", signature);

  const confirmation = await connection.confirmTransaction(
    { signature, ...latest },
    "confirmed",
  );
  if (confirmation.value.err) {
    throw new Error("ATA transaction failed: " + JSON.stringify(confirmation.value.err));
  }

  const created = await connection.getAccountInfo(ata, "finalized");
  if (!created) throw new Error("Finalized ATA account was not found.");
  if (!created.owner.equals(TOKEN_2022_PROGRAM)) {
    throw new Error("ATA owner program verification failed.");
  }
  if (created.data.length !== EXPECTED_ATA_SIZE) {
    throw new Error("ATA data size verification failed.");
  }
  if (!created.data.subarray(0, 32).equals(MINT.toBuffer())) {
    throw new Error("ATA mint verification failed.");
  }
  if (!created.data.subarray(32, 64).equals(payer.toBuffer())) {
    throw new Error("ATA wallet owner verification failed.");
  }
  if (created.data[108] !== 1) {
    throw new Error("ATA initialized-state verification failed.");
  }

  const finalBalance = await connection.getBalance(payer, "finalized");
  console.log({
    result: "PASS",
    cluster: "devnet",
    wallet: payer.toBase58(),
    mint: MINT.toBase58(),
    mintOwner: mintInfo.owner.toBase58(),
    mintDataSize: mintInfo.data.length,
    ata: ata.toBase58(),
    ataBump: bump,
    ataOwnerProgram: created.owner.toBase58(),
    ataDataSize: created.data.length,
    ataInitialized: true,
    rentLamports,
    transactionSignature: signature,
    confirmation: "confirmed",
    startingBalanceLamports: balance,
    finalBalanceLamports: finalBalance,
    liveTrading: "LOCKED",
    mainnetAccess: "BLOCKED",
  });
}

main().catch((error) => {
  console.error("TOKEN2022_PLAYGROUND_E2E_FAILED:", error);
  throw error;
});
