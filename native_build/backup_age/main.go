package main

import (
	"errors"
	"io"
	"os"
	"strings"

	"filippo.io/age"
)

func run() (err error) {
	if len(os.Args) != 4 || (os.Args[1] != "encrypt" && os.Args[1] != "decrypt") {
		return errors.New("invalid operation")
	}
	secret, err := io.ReadAll(io.LimitReader(os.Stdin, 4097))
	if err != nil || len(secret) == 0 || len(secret) > 4096 || strings.ContainsAny(string(secret), "\x00\r\n") {
		return errors.New("invalid passphrase")
	}
	source, err := os.Open(os.Args[2])
	if err != nil {
		return err
	}
	defer source.Close()
	destination, err := os.OpenFile(os.Args[3], os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	defer func() {
		if closeErr := destination.Close(); err == nil {
			err = closeErr
		}
		if err != nil {
			os.Remove(os.Args[3])
		}
	}()
	if os.Args[1] == "encrypt" {
		recipient, recipientErr := age.NewScryptRecipient(string(secret))
		if recipientErr != nil {
			return recipientErr
		}
		recipient.SetWorkFactor(18)
		writer, encryptErr := age.Encrypt(destination, recipient)
		if encryptErr != nil {
			return encryptErr
		}
		if _, err = io.Copy(writer, source); err != nil {
			return err
		}
		if err = writer.Close(); err != nil {
			return err
		}
	} else {
		identity, identityErr := age.NewScryptIdentity(string(secret))
		if identityErr != nil {
			return identityErr
		}
		identity.SetMaxWorkFactor(18)
		reader, decryptErr := age.Decrypt(source, identity)
		if decryptErr != nil {
			return decryptErr
		}
		if _, err = io.Copy(destination, reader); err != nil {
			return err
		}
	}
	return destination.Sync()
}

func main() {
	if run() != nil {
		os.Exit(1)
	}
}